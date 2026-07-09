import os
import numpy as np
import trimesh

from estimater import *
from datareader import *


class FoundationPoseRunner:
    def __init__(
        self,
        mesh_file: str,
        debug_dir: str = "./debug",
        debug: int = 0,
        est_refine_iter: int = 5,
        track_refine_iter: int = 2,
    ):

        set_logging_format()
        set_seed(0)

        self.debug = debug
        self.debug_dir = debug_dir

        self.est_refine_iter = est_refine_iter
        self.track_refine_iter = track_refine_iter

        os.makedirs(debug_dir, exist_ok=True)
        os.makedirs(f"{debug_dir}/track_vis", exist_ok=True)
        os.makedirs(f"{debug_dir}/ob_in_cam", exist_ok=True)

        # -------------------------
        # run_demo.py 그대로
        # -------------------------

        self.mesh = trimesh.load(mesh_file)

        self.to_origin, self.extents = trimesh.bounds.oriented_bounds(
            self.mesh
        )

        self.bbox = np.stack(
            [-self.extents / 2, self.extents / 2],
            axis=0,
        ).reshape(2, 3)

        scorer = ScorePredictor()

        refiner = PoseRefinePredictor()

        glctx = dr.RasterizeCudaContext()

        self.estimator = FoundationPose(
            model_pts=self.mesh.vertices,
            model_normals=self.mesh.vertex_normals,
            mesh=self.mesh,
            scorer=scorer,
            refiner=refiner,
            debug_dir=debug_dir,
            debug=debug,
            glctx=glctx,
        )

        logging.info("FoundationPose initialized")

        self.initialized = False


    def estimate(
        self,
        rgb,
        depth,
        K,
        mask,
    ):
        """
        첫 프레임
        """

        pose = self.estimator.register(
            K=K,
            rgb=rgb,
            depth=depth,
            ob_mask=mask.astype(bool),
            iteration=self.est_refine_iter,
        )

        self.initialized = True

        return pose.reshape(4, 4)


    def tracking(
        self,
        rgb,
        depth,
        K,
    ):
        """
        두 번째 프레임 이후
        """

        if not self.initialized:
            raise RuntimeError("estimate()를 먼저 수행해야 합니다.")

        pose = self.estimator.track_one(
            rgb=rgb,
            depth=depth,
            K=K,
            iteration=self.track_refine_iter,
        )

        return pose.reshape(4, 4)


    def reset(self):
        self.initialized = False


    def estimate_pose(
            self,
            rgb: np.ndarray,
            depth: np.ndarray,
            K: np.ndarray,
            mask: np.ndarray = None,
        ) -> np.ndarray:
            """
            FoundationPose 추론

            Parameters
            ----------
            rgb : RGB 이미지
            depth : Depth 이미지
            K : Camera Intrinsic
            mask : 첫 추론 시 Object Mask (Tracking 시 생략 가능)

            Returns
            -------
            T_tool_hub : np.ndarray (4x4)
            """

            # 첫 추론
            if not self.initialized:

                pose = self.estimator.register(
                    K=K,
                    rgb=rgb,
                    depth=depth,
                    ob_mask=mask.astype(bool),
                    iteration=self.est_refine_iter,
                )

                self.initialized = True

            # Tracking
            else:

                pose = self.estimator.track_one(
                    rgb=rgb,
                    depth=depth,
                    K=K,
                    iteration=self.track_refine_iter,
                )

            pose = pose.reshape(4, 4)

            # Camera → Tool 변환
            T_tool_hub = self.T_tool_camera @ pose

            return T_tool_hub