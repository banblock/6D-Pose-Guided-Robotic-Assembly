#!/usr/bin/env python3
"""ROS2 YOLO instance-segmentation node for hub/part recognition.

Published topics
----------------
/ai_vision/detections       std_msgs/String (JSON)
/ai_vision/annotated_image  sensor_msgs/Image

Service
-------
/ai_vision/get_vision_data    interfaces/srv/GetVisionData
"""

import json
from pathlib import Path
import time
from typing import Any, Dict, List
import numpy as np

import cv2
import rclpy
from ament_index_python.packages import get_package_share_directory
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from interfaces.srv import GetVisionData
import threading



class AIVisionNode(Node):
    """Run a custom Ultralytics model on RealSense RGB frames."""

    def __init__(self) -> None:
        super().__init__("ai_vision")

        package_share = Path(get_package_share_directory("object_detection"))
        default_model = package_share / "resource" / "best.pt"
        # default_model = (
        #     Path.home()
        #     / "ros2_ws"
        #     / "src"
        #     / "object_detection"
        #     / "weights"
        #     / "best.pt"
        # )

        self.declare_parameter("image_topic", "/camera/camera/color/image_raw")
        self.declare_parameter("model_path", str(default_model))
        self.declare_parameter("confidence", 0.50)
        self.declare_parameter("iou", 0.50)
        self.declare_parameter("inference_hz", 10.0)
        self.declare_parameter("show_window", True)
        self.declare_parameter("publish_annotated", True)
        self.declare_parameter("depth_topic", "/camera/camera/aligned_depth_to_color/image_raw")
        self.declare_parameter("depth_scale_m_per_unit", 0.001)
        self.declare_parameter("target_class", "hub")
                
        self.image_topic = str(self.get_parameter("image_topic").value)
        self.model_path = Path(str(self.get_parameter("model_path").value)).expanduser()
        self.confidence = float(self.get_parameter("confidence").value)
        self.iou = float(self.get_parameter("iou").value)
        self.inference_hz = float(self.get_parameter("inference_hz").value)
        self.show_window = bool(self.get_parameter("show_window").value)
        self.publish_annotated = bool(self.get_parameter("publish_annotated").value)
        self.depth_topic = str(self.get_parameter("depth_topic").value)
        self.depth_scale = float(self.get_parameter("depth_scale_m_per_unit").value)
        self.target_class = str(self.get_parameter("target_class").value)



        if self.inference_hz <= 0:
            raise ValueError("inference_hz must be greater than zero.")
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"Model not found: {self.model_path}\n"
                "Train the model first, copy best.pt to resource/hub_part_seg.pt, "
                "and rebuild the package."
            )

        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is not installed. Run: pip install ultralytics"
            ) from exc

        self.model = YOLO(str(self.model_path))
        self.bridge = CvBridge()
        self.min_period = 1.0 / self.inference_hz
        self.last_inference_time = 0.0

        self.data_lock = threading.Lock()

        self.latest_rgb_msg = None
        self.latest_depth_msg = None
        self.latest_mask_msg = None
        self.latest_detections_json = ""
        self.latest_has_target = False
        self.latest_target_class = self.target_class

        self.detections_publisher = self.create_publisher(
            String, "/ai_vision/detections", 10
        )
        self.annotated_publisher = self.create_publisher(
            Image, "/ai_vision/annotated_image", 10
        )
        self.subscription = self.create_subscription(
            Image,
            self.image_topic,
            self._image_callback,
            qos_profile_sensor_data,
        )
        self.depth_subscription = self.create_subscription(
            Image,
            self.depth_topic,
            self._depth_callback,
            qos_profile_sensor_data,
        )
        self.vision_service = self.create_service(
            GetVisionData,
            "/ai_vision/get_vision_data",
            self._get_vision_data_callback,
        )

        self.get_logger().info(f"Loaded model: {self.model_path}")
        self.get_logger().info(f"Image topic: {self.image_topic}")

    @staticmethod
    def _name_for_class(names: Any, class_id: int) -> str:
        if isinstance(names, dict):
            return str(names.get(class_id, class_id))
        if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            return str(names[class_id])
        return str(class_id)
    
    @staticmethod
    def _target_class_from_mode(mode: int) -> str:
        if mode == 0:
            return "hub"
        if mode == 1:
            return "part"
        return "hub"
    
    def _result_to_detections(self, result: Any) -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return detections

        xyxy = boxes.xyxy.cpu().numpy()
        confidences = boxes.conf.cpu().numpy()
        class_ids = boxes.cls.cpu().numpy().astype(int)

        polygons = None
        if result.masks is not None and result.masks.xy is not None:
            polygons = result.masks.xy

        for index, (box, score, class_id) in enumerate(
            zip(xyxy, confidences, class_ids)
        ):
            x1, y1, x2, y2 = [float(value) for value in box]
            polygon = None
            if polygons is not None and index < len(polygons):
                polygon = [
                    [round(float(x), 2), round(float(y), 2)]
                    for x, y in polygons[index]
                ]

            detections.append(
                {
                    "class_id": int(class_id),
                    "class_name": self._name_for_class(result.names, int(class_id)),
                    "confidence": round(float(score), 5),
                    "bbox_xyxy": [
                        round(x1, 2),
                        round(y1, 2),
                        round(x2, 2),
                        round(y2, 2),
                    ],
                    "center_px": [
                        round((x1 + x2) / 2.0, 2),
                        round((y1 + y2) / 2.0, 2),
                    ],
                    "polygon_px": polygon,
                }
            )

        return detections
    
    @staticmethod
    def _has_target_detection(
        detections: List[Dict[str, Any]],
        target_class: str,
    ) -> bool:
        target_lower = target_class.strip().lower()

        for detection in detections:
            class_name = str(detection.get("class_name", "")).strip().lower()
            if class_name == target_lower:
                return True

        return False
    
    def _select_target_mask(
        self,
        result: Any,
        image_height: int,
        image_width: int,
        target_class: str,
    ) -> np.ndarray:
        """
        YOLO segmentation 결과에서 target_class에 해당하는 객체 mask 하나를 선택한다.
        선택 기준:
        1. class_name == target_class
        2. confidence가 가장 높은 객체
        없으면 전부 0인 mask 반환
        """

        mask_image = np.zeros((image_height, image_width), dtype=np.uint8)

        if result.boxes is None or len(result.boxes) == 0:
            return mask_image

        if result.masks is None or result.masks.data is None:
            return mask_image

        class_ids = result.boxes.cls.cpu().numpy().astype(int)
        confidences = result.boxes.conf.cpu().numpy()
        masks = result.masks.data.cpu().numpy()

        target_lower = target_class.strip().lower()

        best_index = None
        best_confidence = -1.0

        for index, class_id in enumerate(class_ids):
            class_name = self._name_for_class(result.names, int(class_id)).strip().lower()

            if class_name != target_lower:
                continue

            confidence = float(confidences[index])

            if confidence > best_confidence:
                best_confidence = confidence
                best_index = index

        if best_index is None:
            return mask_image

        selected_mask = masks[best_index]

        selected_mask = cv2.resize(
            selected_mask,
            (image_width, image_height),
            interpolation=cv2.INTER_NEAREST,
        )

        mask_image[selected_mask > 0.5] = 255

        return mask_image

    def _image_callback(self, msg: Image) -> None:
        now = time.monotonic()
        if now - self.last_inference_time < self.min_period:
            return
        self.last_inference_time = now

        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb_msg = self.bridge.cv2_to_imgmsg(frame_rgb, encoding="rgb8")
            rgb_msg.header = msg.header

            # RGB는 추론 실패 여부와 상관없이 먼저 저장
            with self.data_lock:
                self.latest_rgb_msg = rgb_msg

            h, w = frame.shape[:2]

            roi_mask = np.zeros((h, w), dtype=np.uint8)

            # 기존 고정 ROI 대신 이미지 크기 기준 ROI
            x1 = int(w * 0.3)
            x2 = w
            y1 = 0
            y2 = h
            cv2.rectangle(roi_mask, (x1, y1), (x2, y2), 255, -1)

            frame_roi = cv2.bitwise_and(frame, frame, mask=roi_mask)

            result = self.model.predict(
                source=frame_roi,
                conf=self.confidence,
                iou=self.iou,
                verbose=False,
            )[0]

            detections = self._result_to_detections(result)

            target_class = self.target_class
            mask_image = self._select_target_mask(
                result,
                image_height=h,
                image_width=w,
                target_class=target_class,
            )

            mask_msg = self.bridge.cv2_to_imgmsg(mask_image, encoding="mono8")
            mask_msg.header = msg.header

            payload = {
                "stamp": {
                    "sec": int(msg.header.stamp.sec),
                    "nanosec": int(msg.header.stamp.nanosec),
                },
                "frame_id": msg.header.frame_id,
                "image_width": int(w),
                "image_height": int(h),
                "count": len(detections),
                "detections": detections,
            }

            json_msg = String()
            json_msg.data = json.dumps(payload, ensure_ascii=False)
            self.detections_publisher.publish(json_msg)

            has_target = self._has_target_detection(detections, target_class)

            with self.data_lock:
                self.latest_mask_msg = mask_msg
                self.latest_has_target = has_target
                self.latest_detections_json = json_msg.data
                self.latest_target_class = target_class

            annotated = result.plot()
            annotated[roi_mask == 0] = frame[roi_mask == 0]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)

            if self.publish_annotated:
                annotated_msg = self.bridge.cv2_to_imgmsg(annotated, encoding="bgr8")
                annotated_msg.header = msg.header
                self.annotated_publisher.publish(annotated_msg)

            if self.show_window:
                cv2.imshow("AI Vision - Hub / Part", annotated)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    rclpy.shutdown()

        except Exception as exc:
            self.get_logger().error(f"Inference failed: {repr(exc)}")
            
    def _depth_callback(self, msg: Image) -> None:
        try:
            depth_raw = self.bridge.imgmsg_to_cv2(
                msg,
                desired_encoding="passthrough",
            )

            depth_raw = np.asarray(depth_raw)

            if depth_raw.ndim == 3:
                depth_raw = depth_raw[:, :, 0]

            encoding = str(msg.encoding).upper()

            if encoding == "32FC1" or np.issubdtype(depth_raw.dtype, np.floating):
                depth_m = depth_raw.astype(np.float32, copy=False)
            else:
                depth_m = depth_raw.astype(np.float32) * self.depth_scale

            depth_msg = self.bridge.cv2_to_imgmsg(
                depth_m,
                encoding="32FC1",
            )
            depth_msg.header = msg.header
            with self.data_lock:
                self.latest_depth_msg = depth_msg

        except Exception as exc:
            self.get_logger().error(f"Depth publish failed: {exc}")
    
    def _get_vision_data_callback(self, request, response):
        target_class = self._target_class_from_mode(int(request.mode))

        with self.data_lock:
            rgb_msg = self.latest_rgb_msg
            depth_msg = self.latest_depth_msg
            mask_msg = self.latest_mask_msg
            detections_json = self.latest_detections_json
            has_target = self.latest_has_target
            latest_target_class = self.latest_target_class
            
        if rgb_msg is None:
            response.success = False
            response.message = "rgb image is not ready"
            response.rgb_image = Image()
            response.depth_image = Image()
            response.mask_image = Image()
            response.detections_json = ""
            return response

        if depth_msg is None:
            response.success = False
            response.message = "depth image is not ready"
            response.rgb_image = rgb_msg
            response.depth_image = Image()
            response.mask_image = mask_msg if mask_msg is not None else Image()
            response.detections_json = detections_json
            return response

        if mask_msg is None:
            response.success = False
            response.message = "mask image is not ready"
            response.rgb_image = rgb_msg
            response.depth_image = depth_msg
            response.mask_image = Image()
            response.detections_json = detections_json
            return response

        if latest_target_class != target_class:
            response.success = False
            response.message = f"latest target is {latest_target_class}, requested target is {target_class}"
            response.rgb_image = rgb_msg
            response.depth_image = depth_msg
            response.mask_image = mask_msg
            response.detections_json = detections_json
            return response

        response.rgb_image = rgb_msg
        response.depth_image = depth_msg
        response.mask_image = mask_msg
        response.detections_json = detections_json

        if has_target:
            response.success = True
            response.message = f"{target_class} detected"
        else:
            response.success = False
            response.message = f"{target_class} not detected"

        return response



def main(args=None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = AIVisionNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
