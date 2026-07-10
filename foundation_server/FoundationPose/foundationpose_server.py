import io
import os
import gc
import threading
from typing import Any, Dict, List, Optional
import traceback
import cv2
import numpy as np
import trimesh
import uvicorn
from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel
from scipy.spatial.transform import Rotation 
from estimater import *
from datareader import *  # noqa: F401,F403


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MESH_FILE = os.environ.get(
    "FP_MESH_FILE",
    os.path.join(BASE_DIR, "resource", "hub.obj")
)

DEBUG_DIR = os.environ.get(
    "FP_DEBUG_DIR",
    os.path.join(BASE_DIR, "debug")
)

DEBUG = int(os.environ.get("FP_DEBUG", "0"))
TARGET_W = int(os.environ.get("FP_TARGET_W", "320"))
TARGET_H = int(os.environ.get("FP_TARGET_H", "240"))

EST_REFINE_ITER = int(os.environ.get("FP_EST_REFINE_ITER", "5"))
TRACK_REFINE_ITER = int(os.environ.get("FP_TRACK_REFINE_ITER", "2"))


app = FastAPI(title="FoundationPose HTTP Server")

runtime = None
runtime_lock = threading.Lock()
infer_lock = threading.Lock()


class PoseResponse(BaseModel):
    success: bool
    message: str
    mode: Optional[str] = None
    position: Optional[List[float]] = None
    quaternion_xyzw: Optional[List[float]] = None
    matrix: Optional[List[List[float]]] = None


class FoundationPoseRuntime:
    def __init__(self):
        if not os.path.exists(MESH_FILE):
            raise FileNotFoundError(f"mesh file not found: {MESH_FILE}")

        os.makedirs(DEBUG_DIR, exist_ok=True)

        print("[FoundationPose Server] loading mesh...")
        
        self.mesh = trimesh.load(MESH_FILE)
        self.mesh.apply_scale(0.001)
        self.to_origin, extents = trimesh.bounds.oriented_bounds(self.mesh)

        self.bbox = np.stack(
            [
                -extents / 2,
                extents / 2,
            ],
            axis=0,
        ).reshape(2, 3)

        print("[FoundationPose Server] loading ScorePredictor...")
        self.scorer = ScorePredictor()

        print("[FoundationPose Server] loading PoseRefinePredictor...")
        self.refiner = PoseRefinePredictor()

        print("[FoundationPose Server] creating RasterizeCudaContext...")
        self.glctx = dr.RasterizeCudaContext()

        print("[FoundationPose Server] creating FoundationPose estimator...")
        self.estimator = FoundationPose(
            model_pts=self.mesh.vertices,
            model_normals=self.mesh.vertex_normals,
            mesh=self.mesh,
            scorer=self.scorer,
            refiner=self.refiner,
            debug_dir=DEBUG_DIR,
            debug=DEBUG,
            glctx=self.glctx,
        )

        self.has_registered_pose = False
        self.last_pose = None

        print("[FoundationPose Server] model loaded")
        print(f"[FoundationPose Server] mesh = {MESH_FILE}")
        print(f"[FoundationPose Server] debug = {DEBUG}")
        print(f"[FoundationPose Server] est_refine_iter = {EST_REFINE_ITER}")
        print(f"[FoundationPose Server] track_refine_iter = {TRACK_REFINE_ITER}")

    def register_pose(
        self,
        rgb: np.ndarray,
        depth: np.ndarray,
        mask: np.ndarray,
        K: np.ndarray,
    ) -> np.ndarray:
        rgb, depth, mask, K = self.normalize_like_ycb_reader(rgb, depth, mask, K)
        print("rgb", rgb.shape, rgb.dtype, rgb.min(), rgb.max())
        print("depth", depth.shape, depth.dtype, np.nanmin(depth), np.nanmax(depth))
        print("mask", mask.shape, mask.dtype, mask.min(), mask.max(), mask.sum(), mask.mean())
        print("K", K, K.dtype)
        print("mesh.vertices", self.estimator.mesh.vertices.shape, self.estimator.mesh.vertices.dtype)
        print("mesh.vertex_normals", self.estimator.mesh.vertex_normals.shape, self.estimator.mesh.vertex_normals.dtype)
        pose = self.estimator.register(
            K=K,
            rgb=rgb,
            depth=depth,
            ob_mask=mask,
            iteration=EST_REFINE_ITER,
        )

        self.has_registered_pose = True
        self.last_pose = pose

        return pose

    def track_pose(
        self,
        rgb: np.ndarray,
        depth: np.ndarray,
        K: np.ndarray,
    ) -> np.ndarray:
        if not self.has_registered_pose:
            raise RuntimeError("pose is not registered. call /register first")

        dummy_mask = np.ones(rgb.shape[:2], dtype=np.uint8)

        rgb, depth, _, K = self.normalize_like_ycb_reader(
            rgb=rgb,
            depth=depth,
            mask=dummy_mask,
            K=K,
        )

        if rgb.shape[:2] != depth.shape[:2]:
            raise ValueError(f"rgb/depth size mismatch: rgb={rgb.shape}, depth={depth.shape}")

        if K.shape != (3, 3):
            raise ValueError(f"camera_k must be 3x3, got {K.shape}")

        pose = self.estimator.track_one(
            rgb=rgb,
            depth=depth,
            K=K,
            iteration=TRACK_REFINE_ITER,
        )

        self.last_pose = pose

        return pose

    def reset(self):
        self.has_registered_pose = False
        self.last_pose = None

    @staticmethod
    def _normalize_rgb(rgb: np.ndarray) -> np.ndarray:
        if rgb is None:
            raise ValueError("rgb is None")

        if rgb.ndim == 2:
            rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)

        if rgb.ndim != 3:
            raise ValueError(f"rgb must be HxWx3, got shape={rgb.shape}")

        if rgb.shape[2] == 4:
            rgb = rgb[:, :, :3]

        if rgb.shape[2] != 3:
            raise ValueError(f"rgb channel must be 3, got shape={rgb.shape}")

        return rgb.astype(np.uint8)

    @staticmethod
    def _normalize_depth(depth: np.ndarray) -> np.ndarray:
        if depth is None:
            raise ValueError("depth is None")

        if depth.ndim == 3:
            depth = depth[:, :, 0]

        if depth.ndim != 2:
            raise ValueError(f"depth must be HxW, got shape={depth.shape}")

        depth = depth.astype(np.float32)

        if np.nanmax(depth) > 20.0:
            depth = depth / 1000.0

        depth = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)

        return depth

    @staticmethod
    def _normalize_mask(mask: np.ndarray) -> np.ndarray:
        if mask is None:
            raise ValueError("mask is None")

        if mask.ndim == 3:
            mask = mask[:, :, 0]

        if mask.ndim != 2:
            raise ValueError(f"mask must be HxW, got shape={mask.shape}")

        return (mask > 0).astype(np.uint8)

    def normalize_like_ycb_reader(
        self,
        rgb,
        depth,
        mask,
        K,
        target_w=TARGET_W,
        target_h=TARGET_H,
        zfar=np.inf,
    ):
        K_BASE_W = 1280.0
        K_BASE_H = 720.0

        rgb = rgb[..., :3]
        rgb = cv2.resize(
            rgb,
            (target_w, target_h),
            interpolation=cv2.INTER_NEAREST,
        )
        rgb = rgb.astype(np.uint8)

        if depth.ndim == 3:
            depth = depth[:, :, 0]

        depth = depth.astype(np.float64)

        if np.nanmax(depth) > 20.0:
            depth = depth / 1000.0

        depth = cv2.resize(
            depth,
            (target_w, target_h),
            interpolation=cv2.INTER_NEAREST,
        )
        depth[(depth < 0.001) | (depth >= zfar)] = 0

        if mask.ndim == 3:
            for c in range(3):
                if mask[..., c].sum() > 0:
                    mask = mask[..., c]
                    break

        mask = cv2.resize(
            mask.astype(np.uint8),
            (target_w, target_h),
            interpolation=cv2.INTER_NEAREST,
        )
        mask = mask.astype(bool)

        K = K.astype(np.float64).copy()

        # camera_k.npy가 640x480 기준이므로,
        # 실제 입력 이미지 크기와 무관하게 최종 FoundationPose 입력 크기 기준으로 고정 변환
        scale_x = target_w / K_BASE_W
        scale_y = target_h / K_BASE_H

        K[0, 0] *= scale_x  # fx
        K[1, 1] *= scale_y  # fy
        K[0, 2] *= scale_x  # cx
        K[1, 2] *= scale_y  # cy

        return rgb, depth, mask, K

def pose_to_response(pose: np.ndarray, mode: str, message: str = "ok") -> PoseResponse:
    position = pose[:3, 3].astype(float).tolist()
    quat_xyzw = Rotation.from_matrix(pose[:3, :3]).as_quat().astype(float).tolist()

    return PoseResponse(
        success=True,
        message=message,
        mode=mode,
        position=position,
        quaternion_xyzw=quat_xyzw,
        matrix=pose.astype(float).tolist(),
    )


def load_npz_from_upload(raw: bytes):
    with np.load(io.BytesIO(raw), allow_pickle=False) as npz:
        rgb = npz["rgb"]
        depth = npz["depth"]
        K = npz["camera_k"]
        mask = npz["mask"] if "mask" in npz.files else None

    return rgb, depth, mask, K


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "success": True,
        "message": "server is running",
        "model_loaded": runtime is not None,
        "has_registered_pose": False if runtime is None else runtime.has_registered_pose,
        "mesh_file": MESH_FILE,
        "debug_dir": DEBUG_DIR,
        "debug": DEBUG,
        "est_refine_iter": EST_REFINE_ITER,
        "track_refine_iter": TRACK_REFINE_ITER,
    }


@app.post("/load")
def load_model() -> Dict[str, Any]:
    global runtime

    with runtime_lock:
        if runtime is not None:
            return {
                "success": True,
                "message": "already loaded",
            }

        try:
            runtime = FoundationPoseRuntime()
            return {
                "success": True,
                "message": "model loaded",
            }
        except Exception as e:
            runtime = None
            return {
                "success": False,
                "message": f"model load failed: {repr(e)}",
            }


@app.post("/reset")
def reset_pose() -> Dict[str, Any]:
    if runtime is None:
        return {
            "success": False,
            "message": "model is not loaded. call /load first",
        }

    with infer_lock:
        runtime.reset()

    return {
        "success": True,
        "message": "pose state reset",
    }


@app.post("/unload")
def unload_model() -> Dict[str, Any]:
    global runtime

    with runtime_lock:
        with infer_lock:
            runtime = None
            gc.collect()

            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                print("unload error")

    return {
        "success": True,
        "message": "model unloaded",
    }


@app.post("/register", response_model=PoseResponse)
async def register(data: UploadFile = File(...)):
    global runtime

    if runtime is None:
        return PoseResponse(
            success=False,
            message="model is not loaded. call /load first",
            mode="register",
        )

    try:
        raw = await data.read()
        rgb, depth, mask, K = load_npz_from_upload(raw)

        if mask is None:
            return PoseResponse(
                success=False,
                message="mask is required for /register",
                mode="register",
            )

        with infer_lock:
            pose = runtime.register_pose(
                rgb=rgb,
                depth=depth,
                mask=mask,
                K=K,
            )

        return pose_to_response(
            pose=pose,
            mode="register",
            message="registered",
        )
    except Exception as e:
        traceback.print_exc()
        return PoseResponse(
            success=False,
            message=f"register failed: {repr(e)}",
            mode="register",
        )
    # except Exception as e:
    #     return PoseResponse(
    #         success=False,
    #         message=f"register failed: {repr(e)}",
    #         mode="register",
    #     )


@app.post("/track", response_model=PoseResponse)
async def track(data: UploadFile = File(...)):
    global runtime

    if runtime is None:
        return PoseResponse(
            success=False,
            message="model is not loaded. call /load first",
            mode="track",
        )

    try:
        raw = await data.read()
        rgb, depth, _, K = load_npz_from_upload(raw)

        with infer_lock:
            pose = runtime.track_pose(
                rgb=rgb,
                depth=depth,
                K=K,
            )

        return pose_to_response(
            pose=pose,
            mode="track",
            message="tracked",
        )

    except Exception as e:
        return PoseResponse(
            success=False,
            message=f"track failed: {repr(e)}",
            mode="track",
        )


@app.post("/predict", response_model=PoseResponse)
async def predict(data: UploadFile = File(...)):
    return await register(data)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
    )