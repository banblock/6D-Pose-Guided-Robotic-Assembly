import rclpy

from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from interfaces.srv import (
    AssemblyCommand,
    EstimatePose,
    ExecuteAssembly,
)


class AssemblyController(Node):

    def __init__(self):
        super().__init__("assembly_controller")

        self.callback_group = ReentrantCallbackGroup()

        self.pose_client = self.create_client(
            EstimatePose,
            "/foundationpose/estimate_pose",
            callback_group=self.callback_group,
        )

        self.robot_client = self.create_client(
            ExecuteAssembly,
            "/robot/execute_assembly",
            callback_group=self.callback_group,
        )

        self.command_server = self.create_service(
            AssemblyCommand,
            "/assembly/command",
            self.command_callback,
            callback_group=self.callback_group,
        )

        self.processing = False

    async def command_callback(self, request, response):

        if self.processing:
            response.success = False
            response.message = "Another assembly task is running"
            return response

        self.processing = True

        try:
            if not self.pose_client.wait_for_service(timeout_sec=3.0):
                response.success = False
                response.message = "FoundationPose service unavailable"
                return response

            pose_request = EstimatePose.Request()
            pose_request.mode = 0

            self.get_logger().info(
                "Requesting FoundationPose estimation"
            )

            pose_future = self.pose_client.call_async(
                pose_request
            )

            pose_response = await pose_future

            if pose_response is None:
                response.success = False
                response.message = "Pose service returned None"
                return response

            if not pose_response.success:
                response.success = False
                response.message = (
                    f"Pose estimation failed: "
                    f"{pose_response.message}"
                )
                return response

            posx = list(pose_response.pos)

            if len(posx) != 6:
                response.success = False
                response.message = (
                    f"Invalid gripper_posx length: {len(posx)}"
                )
                return response

            if not self.robot_client.wait_for_service(
                timeout_sec=3.0
            ):
                response.success = False
                response.message = "Robot service unavailable"
                return response

            robot_request = ExecuteAssembly.Request()
            robot_request.face_id = int(request.face_id)
            robot_request.est_pos = posx

            if hasattr(robot_request, "part_id"):
                robot_request.part_id = int(
                    request.part_id
                )

            self.get_logger().info(
                f"Requesting robot assembly: "
                f"face_id={robot_request.face_id}, "
                f"est_pos={robot_request.est_pos}"
            )

            robot_future = self.robot_client.call_async(
                robot_request
            )

            robot_response = await robot_future

            if robot_response is None:
                response.success = False
                response.message = "Robot service returned None"
                return response

            response.success = bool(
                robot_response.success
            )

            response.message = getattr(
                robot_response,
                "message",
                (
                    "Assembly completed"
                    if response.success
                    else "Assembly failed"
                ),
            )

            return response

        except Exception as exc:
            self.get_logger().error(
                f"Assembly command failed: {repr(exc)}"
            )

            response.success = False
            response.message = str(exc)

            return response

        finally:
            self.processing = False

def main(args=None):
    rclpy.init(args=args)

    node = AssemblyController()

    executor = MultiThreadedExecutor(
        num_threads=4
    )
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