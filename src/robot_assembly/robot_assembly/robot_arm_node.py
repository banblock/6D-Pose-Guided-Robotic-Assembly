#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup

from std_srvs.srv import Trigger
from dsr_msgs2.srv import DrlStart

from interfaces.srv import ExecuteAssembly
from robot_assembly import assembly_task


ROBOT_NS = "/dsr01"
ROBOT_SYSTEM_REAL = 0


class FailedStep:
    NONE = 0
    MOVE_TO_SCAN_POSE = 20
    INVALID_FACE = 99


class ErrorCode:
    NONE = 0
    INVALID_REQUEST = 1
    DRL_START_FAILED = 20
    UNKNOWN = 99


class RobotArmNode(Node):
    def __init__(self):
        super().__init__("robot_arm_node")

        self.callback_group = ReentrantCallbackGroup()

        # Doosan DRL 실행 서비스 클라이언트
        self.drl_start_cli = self.create_client(
            DrlStart,
            f"{ROBOT_NS}/drl/drl_start",
            callback_group=self.callback_group,
        )

        # 부품 조립 서비스
        self.assembly_service = self.create_service(
            ExecuteAssembly,
            "/robot/execute_assembly",
            self.handle_execute_assembly,
            callback_group=self.callback_group,
        )

        # 스캔 위치 이동 서비스
        self.scan_pose_service = self.create_service(
            Trigger,
            "/robot/move_to_scan_pose",
            self.handle_move_to_scan_pose,
            callback_group=self.callback_group,
        )

        self.get_logger().info("robot_arm_node started")
        self.get_logger().info("service server: /robot/execute_assembly")
        self.get_logger().info("service server: /robot/move_to_scan_pose")
        self.get_logger().info(f"DRL start client: {ROBOT_NS}/drl/drl_start")
        self.get_logger().info("face_id mapping: 1=top, 2=right, 3=bottom")

    def send_drl_code(self, code: str):
        """
        Doosan DRL 실행 서비스로 문자열 코드를 전송한다.
        여기서는 DRL 시작 성공 여부만 확인한다.
        실제 로봇 동작 완료까지 기다리지는 않는다.
        """

        if not self.drl_start_cli.wait_for_service(timeout_sec=3.0):
            return False, f"{ROBOT_NS}/drl/drl_start service is not ready"

        req = DrlStart.Request()
        req.robot_system = ROBOT_SYSTEM_REAL
        req.code = code

        self.get_logger().info("sending DRL code to Doosan controller")
        self.get_logger().info(f"code length = {len(req.code)}")

        future = self.drl_start_cli.call_async(req)

        while rclpy.ok() and not future.done():
            time.sleep(0.05)

        try:
            result = future.result()

        except Exception as e:
            return False, f"DRL start exception: {e}"

        if not result.success:
            return False, "DRL start rejected"

        return True, "DRL task started"

    def handle_move_to_scan_pose(self, request, response):
        """
        허브 스캔 전 로봇팔을 스캔 위치로 이동시키는 서비스.
        서비스 이름:
            /robot/move_to_scan_pose

        서비스 타입:
            std_srvs/srv/Trigger
        """

        self.get_logger().info("========================================")
        self.get_logger().info("received /robot/move_to_scan_pose request")

        success, message = self.send_drl_code(assembly_task.scan_pose_task)

        response.success = success
        response.message = message

        if success:
            self.get_logger().info("scan pose DRL task started")
        else:
            self.get_logger().error(message)

        self.get_logger().info("========================================")

        return response

    def handle_execute_assembly(self, request, response):
        """
        부품 조립 실행 서비스.
        서비스 이름:
            /robot/execute_assembly

        face_id:
            1 = top
            2 = right
            3 = bottom
        """

        self.get_logger().info("========================================")
        self.get_logger().info("received /robot/execute_assembly request")
        self.get_logger().info(
            f"face_id={request.face_id}, "
        )
        self.get_logger().info(
            f"est_pos={request.est_pos}, "
        )

        if request.face_id not in [1, 2, 3]:
            response.success = False
            response.failed_step = FailedStep.INVALID_FACE
            response.error_code = ErrorCode.INVALID_REQUEST
            response.message = (
                f"unsupported face_id={request.face_id}. "
                "allowed face_id: 1=top, 2=right, 3=bottom"
            )

            self.get_logger().error(response.message)
            self.get_logger().info("========================================")

            return response
        
        if request.est_pos == None:
            response.success = False
            response.failed_step = FailedStep.NONE
            response.error_code = ErrorCode.INVALID_REQUEST
            response.message = (f"unsupported est_pos.")

            self.get_logger().error(response.message)
            self.get_logger().info("========================================")

            return response


        drl_code = (
            f"face_id = {request.face_id}\n"
            f"est_pos = {list(request.est_pos)}\n"
            + assembly_task.assembly_task
        )

        success, message = self.send_drl_code(drl_code)

        if not success:
            response.success = False
            response.failed_step = FailedStep.NONE
            response.error_code = ErrorCode.DRL_START_FAILED
            response.message = message

            self.get_logger().error(response.message)
            self.get_logger().info("========================================")

            return response

        response.success = True
        response.failed_step = FailedStep.NONE
        response.error_code = ErrorCode.NONE
        response.message = message

        self.get_logger().info(response.message)
        self.get_logger().info("========================================")

        return response


def main(args=None):
    rclpy.init(args=args)

    node = RobotArmNode()

    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    try:
        executor.spin()

    except KeyboardInterrupt:
        node.get_logger().info("robot_arm_node interrupted")

    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()