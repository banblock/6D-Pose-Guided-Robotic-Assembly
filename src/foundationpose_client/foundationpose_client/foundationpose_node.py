import io
import json
import os
from typing import Optional

import numpy as np
import requests

import rclpy
from ament_index_python.packages import get_package_share_directory
from cv_bridge import CvBridge
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from scipy.spatial.transform import Rotation as R

from interfaces.srv import EstimatePose, GetVisionData


class FoundationPoseClientNode(Node):
    def __init__(self):
        super().__init__("foundationpose_client_node")

        self.callback_group = ReentrantCallbackGroup()
        self.bridge = CvBridge()
        self.processing = False

        self.declare_parameter("server_base_url", "http://127.0.0.1:8000")
        self.declare_parameter("vision_service", "/ai_vision/get_vision_data")
        self.declare_parameter("camera_k_file", "resource/camera_k.npy")
        self.declare_parameter("auto_load_model", True)

        self.server_base_url = (
            self.get_parameter("server_base_url").value.rstrip("/")
        )
        self.vision_service = self.get_parameter("vision_service").value
        self.camera_k_file = self.get_parameter("camera_k_file").value
        self.auto_load_model = bool(
            self.get_parameter("auto_load_model").value
        )

        self.health_url = f"{self.server_base_url}/health"
        self.load_url = f"{self.server_base_url}/load"
        self.predict_url = f"{self.server_base_url}/predict"

        self.camera_k = self._load_camera_k(self.camera_k_file)
        self.handeye_matrix = self._load_handeye_matrix()

        self.vision_client = self.create_client(
            GetVisionData,
            self.vision_service,
            callback_group=self.callback_group,
        )

        self.pose_server = self.create_service(
            EstimatePose,
            "/foundationpose/estimate_pose",
            self.estimate_pose_callback,
            callback_group=self.callback_group,
        )

        self.get_logger().info("FoundationPoseClientNode started")
        self.get_logger().info(
            "Pose service: /foundationpose/estimate_pose"
        )
        self.get_logger().info(
            f"FoundationPose server: {self.server_base_url}"
        )
        self.get_logger().info(
            f"Vision service: {self.vision_service}"
        )

    async def estimate_pose_callback(self, request, response):
        if self.processing:
            response.success = False
            response.message = "Another pose estimation is running"
            response.pos = []
            return response

        self.processing = True

        try:
            self.get_logger().info(
                f"Pose estimation requested: mode={request.mode}"
            )

            health = self._get_server_health()
            if health is None:
                response.success = False
                response.message = "FoundationPose server unavailable"
                response.pos = []
                return response

            if self.auto_load_model and not health.get("model_loaded", False):
                if not self._request_model_load():
                    response.success = False
                    response.message = "FoundationPose model load failed"
                    response.pos = []
                    return response

            if not self.vision_client.wait_for_service(timeout_sec=3.0):
                response.success = False
                response.message = (
                    f"Vision service unavailable: {self.vision_service}"
                )
                response.pos = []
                return response

            vision_request = GetVisionData.Request()
            vision_request.mode = int(request.mode)

            self.get_logger().info("Requesting RGB-D and mask data")
            vision_future = self.vision_client.call_async(vision_request)
            vision_response = await vision_future

            if vision_response is None:
                response.success = False
                response.message = "Vision service returned None"
                response.pos = []
                return response

            if not vision_response.success:
                response.success = False
                response.message = (
                    f"Vision service failed: {vision_response.message}"
                )
                response.pos = []
                return response

            try:
                rgb = self.bridge.imgmsg_to_cv2(
                    vision_response.rgb_image,
                    desired_encoding="rgb8",
                )
                depth = self.bridge.imgmsg_to_cv2(
                    vision_response.depth_image,
                    desired_encoding="passthrough",
                )
                mask = self.bridge.imgmsg_to_cv2(
                    vision_response.mask_image,
                    desired_encoding="passthrough",
                )
            except Exception as exc:
                response.success = False
                response.message = f"Image conversion failed: {exc}"
                response.pos = []
                return response

            self.get_logger().info(
                f"Vision data received: rgb={rgb.shape}, "
                f"depth={depth.shape}, mask={mask.shape}"
            )

            result = self._request_foundationpose_predict(
                rgb=rgb,
                depth=depth,
                mask=mask,
                camera_k=self.camera_k,
            )

            if result is None:
                response.success = False
                response.message = "FoundationPose prediction failed"
                response.pos = []
                return response

            position = result.get("position")
            quaternion = result.get("quaternion_xyzw")

            if position is None or quaternion is None:
                response.success = False
                response.message = (
                    "FoundationPose result is missing position or quaternion"
                )
                response.pos = []
                return response

            posx = self.camera_pose_to_gripper_posx(
                position,
                quaternion,
            )
            posx = self.format_posx(posx)

            self._log_pose_result(result, posx)

            response.success = True
            response.message = "Pose estimation completed"
            response.pos = posx
            return response

        except Exception as exc:
            self.get_logger().error(
                f"Pose estimation failed: {repr(exc)}"
            )
            response.success = False
            response.message = str(exc)
            response.pos = []
            return response

        finally:
            self.processing = False

    def _load_camera_k(self, path: str) -> np.ndarray:
        if not os.path.isabs(path):
            package_share = get_package_share_directory(
                "foundationpose_client"
            )
            path = os.path.join(package_share, path)

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"camera_k file not found: {path}"
            )

        camera_k = np.load(path)
        if camera_k.shape != (3, 3):
            raise ValueError(
                f"camera_k must have shape (3, 3), got {camera_k.shape}"
            )

        return camera_k

    def _load_handeye_matrix(self) -> np.ndarray:
        package_share = get_package_share_directory(
            "foundationpose_client"
        )
        path = os.path.join(
            package_share,
            "resource",
            "T_gripper2camera.npy",
        )

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"hand-eye matrix file not found: {path}"
            )

        matrix = np.load(path)
        if matrix.shape != (4, 4):
            raise ValueError(
                f"T_gripper2camera must have shape (4, 4), "
                f"got {matrix.shape}"
            )

        return matrix

    def _get_server_health(self) -> Optional[dict]:
        try:
            http_response = requests.get(
                self.health_url,
                timeout=5,
            )
            http_response.raise_for_status()
            result = http_response.json()
        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            self.get_logger().error(
                f"Health check failed: {repr(exc)}"
            )
            return None

        if not result.get("success", False):
            self.get_logger().error(
                f"Health check rejected: {result}"
            )
            return None

        return result

    def _request_model_load(self) -> bool:
        try:
            http_response = requests.post(
                self.load_url,
                timeout=180,
            )
            http_response.raise_for_status()
            result = http_response.json()
        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            self.get_logger().error(
                f"Model load request failed: {repr(exc)}"
            )
            return False

        if not result.get("success", False):
            self.get_logger().error(
                f"Model load failed: {result.get('message')}"
            )
            return False

        self.get_logger().info(
            f"Model load result: {result.get('message')}"
        )
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

        files = {
            "data": (
                "request.npz",
                buffer.getvalue(),
                "application/octet-stream",
            )
        }

        try:
            http_response = requests.post(
                self.predict_url,
                files=files,
                timeout=180,
            )
            http_response.raise_for_status()
            result = http_response.json()
        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            self.get_logger().error(
                f"Predict request failed: {repr(exc)}"
            )
            return None

        if not result.get("success", False):
            self.get_logger().error(
                f"FoundationPose prediction rejected: "
                f"{result.get('message')}"
            )
            return None

        return result

    def camera_pose_to_gripper_posx(
        self,
        position,
        quaternion,
    ) -> list[float]:
        position_array = np.asarray(position, dtype=np.float64)
        quaternion_array = np.asarray(quaternion, dtype=np.float64)

        if position_array.shape != (3,):
            raise ValueError(
                f"position must have length 3, got {position_array.shape}"
            )

        if quaternion_array.shape != (4,):
            raise ValueError(
                f"quaternion must have length 4, "
                f"got {quaternion_array.shape}"
            )

        quaternion_norm = np.linalg.norm(quaternion_array)
        if quaternion_norm < 1e-8:
            raise ValueError("quaternion norm is zero")

        quaternion_array /= quaternion_norm

        position_mm = position_array * 1000.0

        t_camera_hub = np.eye(4, dtype=np.float64)
        t_camera_hub[:3, :3] = R.from_quat(
            quaternion_array
        ).as_matrix()
        t_camera_hub[:3, 3] = position_mm

        t_gripper_hub = self.handeye_matrix @ t_camera_hub

        position_gripper_mm = t_gripper_hub[:3, 3]
        rotation_vector_deg = np.rad2deg(
            R.from_matrix(
                t_gripper_hub[:3, :3]
            ).as_rotvec()
        )

        return [
            float(position_gripper_mm[0]),
            float(position_gripper_mm[1]),
            float(position_gripper_mm[2]),
            float(rotation_vector_deg[0]),
            float(rotation_vector_deg[1]),
            float(rotation_vector_deg[2]),
        ]

    @staticmethod
    def format_posx(posx) -> list[float]:
        if len(posx) != 6:
            raise ValueError(
                f"posx must have length 6, got {len(posx)}"
            )
        posx[2] = 0
        posx[4] = 0
        return [float(round(value, 2)) for value in posx]

    def _log_pose_result(self, result: dict, posx: list[float]):
        self.get_logger().info(
            "========== FoundationPose Result =========="
        )
        self.get_logger().info(
            f"position [x, y, z] = {result.get('position')}"
        )
        self.get_logger().info(
            "quaternion [x, y, z, w] = "
            f"{result.get('quaternion_xyzw')}"
        )
        self.get_logger().info(
            "T_camera_hub =\n"
            f"{json.dumps(result.get('matrix'), indent=2)}"
        )
        self.get_logger().info(f"gripper posx = {posx}")
        self.get_logger().info(
            "==========================================="
        )


def main(args=None):
    rclpy.init(args=args)

    node = FoundationPoseClientNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.remove_node(node)
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()