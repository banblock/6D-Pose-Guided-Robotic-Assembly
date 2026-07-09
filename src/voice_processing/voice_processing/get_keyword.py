import os
import rclpy
import pyaudio
import json
from rclpy.node import Node

from ament_index_python.packages import get_package_share_directory
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate  # d2 이거를 langchain_core로 바꿈
# from langchain.chains import LLMChain

from voice_processing.MicController import MicController, MicConfig

from voice_processing.wakeup_word import WakeupWord
from voice_processing.stt import STT

import threading
import time
from interfaces.srv import VoiceCommand

############ Package Path & Environment Setting ############

#----------------------------------------------------------------
# current_dir = os.getcwd()
# package_path = get_package_share_directory("pick_and_place_voice")

# env_path = "/home/rokey/cobot_ws/src/cobot2_ws/pick_and_place_voice/resource/.env"
# load_dotenv(dotenv_path=env_path)
# is_load = load_dotenv(dotenv_path=os.path.join(f"{package_path}/resource/.env"))
# openai_api_key = os.getenv("OPENAI_API_KEY")
#-----------------------------------------------------------------

PACKAGE_NAME = "voice_processing"
PACKAGE_PATH = get_package_share_directory(PACKAGE_NAME)
RESOURCE_PATH = os.path.join(PACKAGE_PATH, "resource")
ENV_PATH = os.path.join(RESOURCE_PATH, ".env")
load_dotenv(dotenv_path=ENV_PATH)
openai_api_key = os.getenv("OPENAI_API_KEY")

############ AI Processor ############
# class AIProcessor:
#     def __init__(self):



############ GetKeyword Node ############
class GetKeyword(Node):
    def __init__(self):

        print(PACKAGE_PATH, RESOURCE_PATH, ENV_PATH)

        self.llm = ChatOpenAI(
            model="gpt-4o", temperature=0.5, openai_api_key=openai_api_key
        )

        prompt_content = """
            당신은 사용자의 음성 명령 문장에서 조립할 면 번호와 부품 번호를 추출해야 합니다.

            <목표>
            - 사용자의 문장에서 조립면과 부품 번호를 추출하세요.
            - 조립면은 반드시 a, b, c, d 중 하나입니다.
            - 부품은 반드시 part1, part2, part3 중 하나입니다.
            - 문장에 조립면이 없으면 face는 빈 문자열 ""로 출력하세요.
            - 문장에 부품 번호가 없으면 part는 빈 문자열 ""로 출력하세요.

            <조립면 변환 규칙>
            - a면, A면, 에이면, 에이 면 → a
            - b면, B면, 비면, 비 면 → b
            - c면, C면, 씨면, 씨 면 → c
            - d면, D면, 디면, 디 면 → d

            <부품 변환 규칙>
            - 부품1, 부품 1, 1번 부품, 일번 부품 → part1
            - 부품2, 부품 2, 2번 부품, 이번 부품 → part2
            - 부품3, 부품 3, 3번 부품, 삼번 부품 → part3

            <출력 형식>
            반드시 아래 형식만 출력하세요.
            설명 문장, 마침표, 코드블록은 출력하지 마세요.

            face / part

            <예시>
            입력: "a면에 부품1 조립해줘"
            출력: a / part1

            입력: "씨 면에 삼번 부품 넣어줘"
            출력: c / part3

            입력: "부품1 잡아줘"
            출력:  / part1

            입력: "d면으로 가"
            출력: d / 

            입력: "다시 해줘"
            출력:  / 

            입력 문장:
            "{user_input}"
        """

        self.prompt_template = PromptTemplate(
            input_variables=["user_input"], template=prompt_content
        )
        self.lang_chain = self.prompt_template | self.llm
        # self.lang_chain = LLMChain(llm=self.llm, prompt=self.prompt_template)
        self.stt = STT(openai_api_key=openai_api_key)


        super().__init__("get_keyword_node")
        # 오디오 설정
        mic_config = MicConfig(
            chunk=12000,
            rate=48000,
            channels=1,
            record_seconds=5,
            fmt=pyaudio.paInt16,
            device_index=10,
            buffer_size=24000,
        )
        self.mic_controller = MicController(config=mic_config)
        # self.ai_processor = AIProcessor()

        self.get_logger().info("MicRecorderNode initialized.")
        self.get_logger().info("wait for client's request...")
        self.command_client = self.create_client(
            VoiceCommand, "/voice_command"
        )
        self.wakeup_word = WakeupWord(mic_config.buffer_size)
        self.voice_thread = threading.Thread(
            target=self.voice_loop, daemon=True
        )
        self.voice_thread.start()

    def extract_keyword(self, output_message):  # d2 이 함수 일부 수정함
        response = self.lang_chain.invoke({"user_input": output_message})
        result = response.content.strip()

        face, part = result.strip().split("/")

        face = face.strip()
        part = part.strip()

        print(f"face: {face}")
        print(f"part: {part}")
        return face, part
    
    def voice_loop(self):
        try:
            print("open stream")
            self.mic_controller.open_stream()
            self.wakeup_word.set_stream(self.mic_controller.stream)
        except OSError:
            self.get_logger().error("Error: Failed to open audio stream")
            self.get_logger().error("please check your device index")
            return

        while rclpy.ok():
            self.get_logger().info("음성 명령 대기중... 작업을 시작하려면 Hello Rokey라고 불러주세요.")

            while rclpy.ok() and not self.wakeup_word.is_wakeup():
                time.sleep(0.01)

            if not rclpy.ok():
                break

            self.get_logger().info("Wakeup word detected. 명령을 말해주세요.")

            output_message = self.stt.speech2text()
            face, part = self.extract_keyword(output_message)

            self.get_logger().warn(f"Detected command: face={face}, part={part}")

            if not self.is_valid_command(face, part):
                self.get_logger().warn("잘못된 명령입니다. face 또는 part가 비어있습니다.")
                continue

            self.send_assembly_command(face, part)
            return

    def is_valid_command(self, face, part):
        valid_faces = ["a", "b", "c", "d"]
        valid_parts = ["part1", "part2", "part3"]
        return face in valid_faces and part in valid_parts

    def send_assembly_command(self, face, part):
        if not self.command_client.wait_for_service(timeout_sec=5.0):
            self.get_logger().error("Service /voice_command is not available.")
            return
        request = VoiceCommand.Request()
        request.face = face
        request.part = part
        self.get_logger().info(f"Sending command to /voice_command: face={face}, part={part}")
        future = self.command_client.call_async(request)
        future.add_done_callback(self.voice_command_response_callback)
    
    def voice_command_response_callback(self, future):
        try:
            response = future.result()
        except Exception as e:
            self.get_logger().error(f"Service call failed: {e}")
            return
        if response.success:
            self.get_logger().info(f"Service call succeeded: {response.message}")
        else:
            self.get_logger().error(f"Service call failed: {response.message}")
        
        if rclpy.ok():
            rclpy.shutdown()

def main():  # d2 메인문 일부 수정
    rclpy.init()
    node = GetKeyword()
    rclpy.spin(node)
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()


if __name__ == "__main__":
    main()
