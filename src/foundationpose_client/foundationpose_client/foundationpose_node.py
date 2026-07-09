import io
import json
import os
from typing import Optional

import numpy as np
import requests

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from cv_bridge import CvBridge
from ament_index_python.packages import get_package_share_directory
import cv2
from interfaces.srv import GetVisionData


class FoundationPoseClientNode(Node):
    def __init__(self):
        super().__init__("foundationpose_client_node")

        self.bridge = CvBridge()

        self.declare_parameter("server_base_url", "http://127.0.0.1:8000")
        self.declare_parameter("vision_service", "/ai_vision/get_vision_data")
        self.declare_parameter("camera_k_file", "resource/camera_k.npy")
        self.declare_parameter("mode", 0)
        self.declare_parameter("auto_load_model", True)

        self.server_base_url = self.get_parameter("server_base_url").value.rstrip("/")
        self.vision_service = self.get_parameter("vision_service").value
        self.camera_k_file = self.get_parameter("camera_k_file").value
        self.mode = int(self.get_parameter("mode").value)
        self.auto_load_model = bool(self.get_parameter("auto_load_model").value)

        self.health_url = f"{self.server_base_url}/health"
        self.load_url = f"{self.server_base_url}/load"
        self.predict_url = f"{self.server_base_url}/predict"

        self.camera_k = self._load_camera_k(self.camera_k_file)

        self.vision_client = self.create_client(
            GetVisionData,
            self.vision_service,
        )

        self.already_requested = False
        self.processing = False

        self.timer = self.create_timer(1.0, self.run_once)

        self.get_logger().info("FoundationPoseClientNode started")
        self.get_logger().info(f"server_base_url = {self.server_base_url}")
        self.get_logger().info(f"vision_service = {self.vision_service}")
        self.get_logger().info(f"camera_k =\n{self.camera_k}")

    def _load_camera_k(self, path: str) -> np.ndarray:
        if not os.path.isabs(path):
            package_share = get_package_share_directory("foundationpose_client")
            path = os.path.join(package_share, path)

        if not os.path.exists(path):
            raise FileNotFoundError(f"camera_k file not found: {path}")

        return np.load(path).astype(np.float32)

    def run_once(self):
        if self.already_requested or self.processing:
            return

        self.already_requested = True
        self.processing = True
        self.request_pose_async()

    def request_pose_async(self):
        self.get_logger().info("[STEP 1] Checking FoundationPose server")

        if not self._check_server_health():
            self.get_logger().error("FoundationPose server is not available")
            self.processing = False
            return

        if self.auto_load_model:
            self.get_logger().info("[STEP 2] Loading FoundationPose model")
            if not self._request_model_load():
                self.get_logger().error("FoundationPose model load failed")
                self.processing = False
                return

        self.get_logger().info("[STEP 3] Waiting for vision service")

        if not self.vision_client.wait_for_service(timeout_sec=3.0):
            self.get_logger().error(
                f"Vision service not available: {self.vision_service}"
            )
            self.processing = False
            return

        req = GetVisionData.Request()
        req.mode = self.mode

        self.get_logger().info("[STEP 4] Sending vision service request")
        future = self.vision_client.call_async(req)
        future.add_done_callback(self._on_vision_data_response)

    def _on_vision_data_response(self, future):
        try:
            res = future.result()
        except Exception as exc:
            self.get_logger().error(f"Vision service exception: {repr(exc)}")
            self.processing = False
            return

        if res is None:
            self.get_logger().error("Vision service returned None")
            self.processing = False
            return

        if not res.success:
            self.get_logger().error(f"Vision service failed: {res.message}")
            self.processing = False
            return

        try:
            rgb = self.bridge.imgmsg_to_cv2(
                res.rgb_image,
                desired_encoding="rgb8",
            )
            depth = self.bridge.imgmsg_to_cv2(
                res.depth_image,
                desired_encoding="passthrough",
            )
            mask = self.bridge.imgmsg_to_cv2(
                res.mask_image,
                desired_encoding="passthrough",
            )
        except Exception as exc:
            self.get_logger().error(f"Image conversion failed: {repr(exc)}")
            self.processing = False
            return

        rgb, depth, mask, camera_k = self._resize_inputs(
            rgb=rgb,
            depth=depth,
            mask=mask,
            camera_k=self.camera_k,
            max_width=640,
        )

        self.get_logger().info("[STEP 5] Vision data received")
        self.get_logger().info(
            f"rgb shape={rgb.shape}, depth shape={depth.shape}, mask shape={mask.shape}"
        )

        result = self._request_foundationpose_predict(
            rgb=rgb,
            depth=depth,
            mask=mask,
            camera_k=camera_k,
        )

        if result is None:
            self.get_logger().error("FoundationPose predict request failed")
            self.processing = False
            return

        self._print_pose_result(result)
        self.processing = False

    def _check_server_health(self) -> bool:
        try:
            response = requests.get(
                self.health_url,
                timeout=5,
            )
        except requests.RequestException as exc:
            self.get_logger().error(f"Health request error: {repr(exc)}")
            return False

        if response.status_code != 200:
            self.get_logger().error(
                f"Health HTTP error: {response.status_code}, body={response.text}"
            )
            return False

        result = response.json()

        if not result.get("success", False):
            self.get_logger().error(f"Health check failed: {result}")
            return False

        self.get_logger().info(
            f"Server health ok, model_loaded={result.get('model_loaded')}"
        )

        return True

    def _request_model_load(self) -> bool:
        try:
            response = requests.post(
                self.load_url,
                timeout=180,
            )
        except requests.RequestException as exc:
            self.get_logger().error(f"Model load request error: {repr(exc)}")
            return False

        if response.status_code != 200:
            self.get_logger().error(
                f"Model load HTTP error: {response.status_code}, body={response.text}"
            )
            return False

        result = response.json()

        if not result.get("success", False):
            self.get_logger().error(f"Model load failed: {result.get('message')}")
            return False

        self.get_logger().info(f"Model load result: {result.get('message')}")
        return True

    def _request_foundationpose_predict(
        self,
        rgb: np.ndarray,
        depth: np.ndarray,
        mask: np.ndarray,
        camera_k: np.ndarray,
    ) -> Optional[dict]:
        buffer = io.BytesIO()
        
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        depth = np.ascontiguousarray(depth, dtype=np.float32)
        mask = np.ascontiguousarray(mask, dtype=np.uint8)
        camera_k = np.ascontiguousarray(camera_k, dtype=np.float32)

        np.savez_compressed(
            buffer,
            rgb=rgb,
            depth=depth,
            mask=mask,
            camera_k=camera_k,
        )

        buffer.seek(0)

        files = {
            "data": (
                "request.npz",
                buffer.getvalue(),
                "application/octet-stream",
            )
        }

        self.get_logger().info("[STEP 6] Sending FoundationPose predict request")

        try:
            response = requests.post(
                self.predict_url,
                files=files,
                timeout=180,
            )
        except requests.RequestException as exc:
            self.get_logger().error(f"Predict HTTP request error: {repr(exc)}")
            return None

        if response.status_code != 200:
            self.get_logger().error(
                f"Predict HTTP status error: {response.status_code}, body={response.text}"
            )
            return None

        result = response.json()

        if not result.get("success", False):
            self.get_logger().error(
                f"FoundationPose predict failed: {result.get('message')}"
            )
            return None

        return result

    def _print_pose_result(self, result: dict):
        position = result.get("position")
        quaternion = result.get("quaternion_xyzw")
        matrix = result.get("matrix")

        self.get_logger().info("========== FoundationPose Result ==========")
        self.get_logger().info(f"position [x, y, z] = {position}")
        self.get_logger().info(f"quaternion [x, y, z, w] = {quaternion}")
        self.get_logger().info(f"T_camera_hub =\n{json.dumps(matrix, indent=2)}")
        self.get_logger().info("===========================================")


    def _resize_inputs(
        self,
        rgb: np.ndarray,
        depth: np.ndarray,
        mask: np.ndarray,
        camera_k: np.ndarray,
        max_width: int = 640,
    ):
        h, w = rgb.shape[:2]

        if w <= max_width:
            return rgb, depth, mask, camera_k

        scale = max_width / float(w)
        new_w = int(w * scale)
        new_h = int(h * scale)

        rgb_resized = cv2.resize(
            rgb,
            (new_w, new_h),
            interpolation=cv2.INTER_AREA,
        )

        depth_resized = cv2.resize(
            depth,
            (new_w, new_h),
            interpolation=cv2.INTER_NEAREST,
        )

        mask_resized = cv2.resize(
            mask,
            (new_w, new_h),
            interpolation=cv2.INTER_NEAREST,
        )

        camera_k_resized = camera_k.copy()
        camera_k_resized[0, 0] *= scale  # fx
        camera_k_resized[1, 1] *= scale  # fy
        camera_k_resized[0, 2] *= scale  # cx
        camera_k_resized[1, 2] *= scale  # cy

        return rgb_resized, depth_resized, mask_resized, camera_k_resized

def main(args=None):
    rclpy.init(args=args)

    node = FoundationPoseClientNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.remove_node(node)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()