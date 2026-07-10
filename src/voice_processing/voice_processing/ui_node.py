import os
import sys
import threading
import time
from typing import Optional, Tuple

import rclpy
from rclpy.node import Node

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

try:
    from interfaces.srv import AssemblyCommand
except ImportError as exc:
    AssemblyCommand = None
    _ASSEMBLY_IMPORT_ERROR = exc
else:
    _ASSEMBLY_IMPORT_ERROR = None

try:
    import pyaudio
    from ament_index_python.packages import get_package_share_directory
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import PromptTemplate

    from voice_processing.MicController import MicController, MicConfig
    from voice_processing.wakeup_word import WakeupWord
    from voice_processing.stt import STT
except ImportError as exc:
    _VOICE_IMPORT_ERROR = exc
else:
    _VOICE_IMPORT_ERROR = None

try:
    from .ui_window import AssemblyUI
except ImportError:
    from ui_window import AssemblyUI


class VoiceUISignals(QObject):
    """음성 처리 스레드에서 Qt UI 스레드로 데이터를 넘기기 위한 signal."""

    voice_text_received = pyqtSignal(str)       # STT 원문
    command_received = pyqtSignal(str, str)     # part, face 표시값
    voice_state_received = pyqtSignal(str, str) # waiting / recording / unrecognized


class KeywordListener:
    """get_keyword.py의 음성 인식/키워드 추출 기능을 UI 내부에서 직접 실행하는 클래스.

    중요한 점:
        - ROS2 Node가 아니다.
        - /voice_command 서비스를 만들거나 호출하지 않는다.
        - 음성 처리 진행 상태는 왼쪽 음성 인식 영역으로만 보낸다.
        - 오른쪽 작업 상태 영역에는 음성 로그를 보내지 않는다.
        - 정상 명령을 인식하면 취소/작업 종료 전까지 음성 인식을 일시정지한다.
    """

    def __init__(self, logger, signals: VoiceUISignals):
        self.logger = logger
        self.signals = signals
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

        self.llm = None
        self.lang_chain = None
        self.stt = None
        self.mic_controller = None
        self.wakeup_word = None

        self._init_voice_modules()

    def _emit(self, signal, *args):
        """UI 종료 중 삭제된 Qt 객체에 emit하지 않도록 보호."""
        if self.stop_event.is_set():
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            # UI가 이미 닫힌 경우. 스레드도 종료 방향으로 전환한다.
            self.stop_event.set()

    def _init_voice_modules(self):
        if _VOICE_IMPORT_ERROR is not None:
            self.logger.error(f"음성 처리 모듈 import 실패: {_VOICE_IMPORT_ERROR}")
            self._emit(
                self.signals.voice_state_received,
                "unrecognized",
                "음성 처리 모듈 import 실패",
            )
            return

        package_name = "voice_processing"
        package_path = get_package_share_directory(package_name)
        resource_path = os.path.join(package_path, "resource")
        env_path = os.path.join(resource_path, ".env")

        load_dotenv(dotenv_path=env_path)
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not openai_api_key:
            self.logger.error(f"OPENAI_API_KEY를 찾지 못했습니다: {env_path}")
            self._emit(
                self.signals.voice_state_received,
                "unrecognized",
                "OPENAI_API_KEY를 찾지 못했습니다",
            )
            return

        self.logger.info(f"voice_processing package: {package_path}")
        self.logger.info(f"voice env: {env_path}")

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

        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0.5,
            openai_api_key=openai_api_key,
        )
        prompt_template = PromptTemplate(
            input_variables=["user_input"],
            template=prompt_content,
        )
        self.lang_chain = prompt_template | self.llm
        self.stt = STT(openai_api_key=openai_api_key)

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

        # debug=False이므로 confidence가 터미널에 계속 출력되지 않는다.
        try:
            self.wakeup_word = WakeupWord(mic_config.buffer_size, debug=False)
        except TypeError:
            # 아직 wakeup_word.py가 debug 인자를 지원하지 않아도 실행 자체는 되게 둔다.
            self.wakeup_word = WakeupWord(mic_config.buffer_size)

        # 여기서는 waiting 신호를 보내지 않는다.
        # 실제 음성 인식은 UI 시작 버튼을 눌러 2번 화면이 표시된 뒤 start()에서 시작한다.

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            self.resume()
            return

        if self.lang_chain is None or self.stt is None or self.mic_controller is None:
            self.logger.error("음성 처리 초기화 실패로 KeywordListener를 시작할 수 없습니다")
            self._emit(self.signals.voice_state_received, "unrecognized", "음성 처리 초기화 실패")
            return

        self.stop_event.clear()
        self.pause_event.clear()
        self.thread = threading.Thread(target=self.voice_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self._close_mic_stream()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def pause(self):
        """정상 명령이 들어온 뒤 취소/작업 종료 전까지 음성 인식을 멈춘다."""
        self.pause_event.set()

    def resume(self):
        """취소 또는 작업 종료 후 다시 Hello Rokey 대기로 복귀한다."""
        if self.stop_event.is_set():
            return
        self.pause_event.clear()
        self._emit(self.signals.voice_state_received, "waiting", "")

    def _close_mic_stream(self):
        if self.mic_controller is None:
            return

        # 프로젝트의 MicController 구현에 따라 메서드 이름이 다를 수 있어 방어적으로 처리한다.
        for method_name in ("close_stream", "close"):
            method = getattr(self.mic_controller, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
                return

        stream = getattr(self.mic_controller, "stream", None)
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def extract_keyword(self, output_message: str) -> Tuple[str, str]:
        response = self.lang_chain.invoke({"user_input": output_message})
        result = response.content.strip()

        if "/" not in result:
            self.logger.warn(f"키워드 추출 형식 오류: {result}")
            return "", ""

        face, part = result.split("/", 1)
        return face.strip(), part.strip()

    @staticmethod
    def is_valid_command(face: str, part: str) -> bool:
        valid_faces = {"a", "b", "c", "d"}
        valid_parts = {"part1", "part2", "part3"}
        return face in valid_faces and part in valid_parts

    @staticmethod
    def is_prompt_echo(text: str) -> bool:
        """무음/불명확 입력에서 Whisper가 prompt 일부를 그대로 반환한 경우를 걸러낸다."""
        normalized = " ".join(str(text).strip().split())
        if not normalized:
            return True

        prompt_echo_markers = [
            "사용자는",
            "조립 명령입니다",
            "중 하나를 말합니다",
            "a면, b면",
            "부품1, 부품2",
        ]
        return any(marker in normalized for marker in prompt_echo_markers)

    @staticmethod
    def to_display_values(face: str, part: str) -> Tuple[str, str]:
        """LLM 결과 a/part3 → UI 표시값 .part='3', face='A'로 변환"""
        display_face = face.upper()
        display_part = None
        return display_part, display_face

    def _show_unrecognized_then_waiting(self, detail: str = ""):
        self._emit(self.signals.voice_state_received, "unrecognized", detail)
        # 오류 메시지는 최소 1초 동안 보이게 둔다.
        slept = 0.0
        while slept < 1.0 and not self.stop_event.is_set() and not self.pause_event.is_set():
            time.sleep(0.1)
            slept += 0.1
        if not self.stop_event.is_set() and not self.pause_event.is_set():
            self._emit(self.signals.voice_state_received, "waiting", "")

    def voice_loop(self):
        try:
            self.logger.info("open mic stream")
            self.mic_controller.open_stream()
            self.wakeup_word.set_stream(self.mic_controller.stream)
        except OSError:
            self.logger.error("마이크 스트림을 열지 못했습니다. device_index를 확인하세요")
            self._emit(
                self.signals.voice_state_received,
                "unrecognized",
                "마이크 스트림 열기 실패. device_index를 확인하세요",
            )
            return
        except Exception as exc:
            self.logger.error(f"마이크 초기화 실패: {exc}")
            self._emit(self.signals.voice_state_received, "unrecognized", f"마이크 초기화 실패: {exc}")
            return

        self._emit(self.signals.voice_state_received, "waiting", "")

        while rclpy.ok() and not self.stop_event.is_set():
            if self.pause_event.is_set():
                time.sleep(0.1)
                continue

            while rclpy.ok() and not self.stop_event.is_set() and not self.pause_event.is_set():
                try:
                    if self.wakeup_word.is_wakeup():
                        break
                except Exception as exc:
                    self.logger.error(f"Wakeup word 처리 실패: {exc}")
                    self._show_unrecognized_then_waiting(f"Wakeup word 처리 실패: {exc}")
                time.sleep(0.01)

            if not rclpy.ok() or self.stop_event.is_set() or self.pause_event.is_set():
                continue

            self.logger.info("Wakeup word detected")
            self._emit(self.signals.voice_state_received, "recording", "")

            try:
                output_message = self.stt.speech2text()
            except Exception as exc:
                self.logger.error(f"STT 처리 실패: {exc}")
                self._show_unrecognized_then_waiting(f"STT 처리 실패: {exc}")
                continue

            if self.stop_event.is_set() or self.pause_event.is_set():
                continue

            output_message = str(output_message).strip()
            self.logger.info(f"STT 결과: {output_message}")

            if not output_message or self.is_prompt_echo(output_message):
                self._show_unrecognized_then_waiting("")
                continue

            self._emit(self.signals.voice_text_received, output_message)

            try:
                face, part = self.extract_keyword(output_message)
            except Exception as exc:
                self.logger.error(f"키워드 추출 실패: {exc}")
                self._show_unrecognized_then_waiting(
                    f"입력 받은 문장: {output_message}\n키워드 추출 실패"
                )
                continue

            self.logger.info(f"Detected command: face={face}, part={part}")

            # if not self.is_valid_command(face, part):
            #     self._show_unrecognized_then_waiting(
            #         f"입력 받은 문장: {output_message}\n부품과 면을 모두 말해주세요."
            #     )
            #     continue

            display_part, display_face = self.to_display_values(face, part)

            # 정상 명령을 받으면 UI 확인/취소 전까지 음성 인식을 멈춘다.
            self.pause()
            self._emit(self.signals.command_received, display_part, display_face)

        self._close_mic_stream()


class UINode(Node):
    """UI 내부 음성 인식 + manager_node AssemblyCommand 서비스 클라이언트.

    현재 포함 기능:
        - get_keyword 기능을 UI 노드 내부에서 직접 실행
        - 음성 인식 진행 상태를 UI 왼쪽 음성 영역에 직접 표시
        - 선택 버튼 클릭 시 /assembly/command 서비스 호출

    현재 제외 기능:
        - voice 노드와 ROS2 서비스/토픽 통신
        - 작업 상태 topic 구독
        - 오류 topic 구독
    """

    def __init__(self, window: AssemblyUI):
        super().__init__("ui_node")
        self.window = window

        self.voice_signals = VoiceUISignals()
        self.voice_signals.voice_text_received.connect(self.window.set_voice_command)
        self.voice_signals.command_received.connect(self.window.set_command)
        self.voice_signals.voice_state_received.connect(self.window.set_voice_state)

        self.keyword_listener = KeywordListener(self.get_logger(), self.voice_signals)

        # 시작 버튼을 눌러 2번 화면으로 넘어간 뒤에만 음성 인식 시작
        self.window.signals.voice_start_requested.connect(self.start_voice_listener)
        self.window.signals.voice_resume_requested.connect(self.resume_voice_listener)

        if AssemblyCommand is None:
            self.command_client = None
            self.get_logger().error(
                f"interfaces.srv.AssemblyCommand를 import하지 못했습니다: {_ASSEMBLY_IMPORT_ERROR}"
            )
            self.window.add_status_log("AssemblyCommand srv 타입을 찾지 못했습니다")
            return

        self.command_client = self.create_client(
            AssemblyCommand,
            "/assembly/command",
        )

        self.window.signals.command_confirmed.connect(self.send_assembly_command)

    def start_voice_listener(self):
        """UI 안내문이 먼저 표시된 뒤 음성 인식을 시작한다."""
        self.keyword_listener.resume()
        QTimer.singleShot(100, self.keyword_listener.start)

    def resume_voice_listener(self):
        """취소 또는 작업 종료 후 음성 인식을 다시 켠다."""
        self.keyword_listener.resume()
        if self.keyword_listener.thread is None or not self.keyword_listener.thread.is_alive():
            QTimer.singleShot(100, self.keyword_listener.start)

    def send_assembly_command(self, part_id: int, face_id: int):
        """선택 버튼 클릭 시 manager_node에 part_id, face_id 전송."""
        if self.command_client is None:
            self.window.handle_command_response(False, "서비스 타입이 없어 명령을 보낼 수 없습니다")
            return

        if not self.command_client.wait_for_service(timeout_sec=1.0):
            msg = "/assembly/command 서비스를 찾지 못했습니다"
            self.get_logger().warn(msg)
            self.window.handle_command_response(False, msg)
            return

        request = AssemblyCommand.Request()
        request.part_id = 1
        request.face_id = int(face_id)

        # 서비스 응답 대기 중에는 중복 확인만 막고, 작업 취소 버튼은 유지한다.
        self.window.set_command_pending()

        future = self.command_client.call_async(request)
        future.add_done_callback(self.on_command_response)

    def on_command_response(self, future):
        try:
            response = future.result()
        except Exception as exc:
            msg = f"서비스 응답 처리 실패: {exc}"
            self.get_logger().error(msg)
            self.window.handle_command_response(False, msg)
            return

        success = bool(response.success)
        message = str(response.message)
        self.get_logger().info(
            f"AssemblyCommand response: success={success}, message={message}"
        )
        self.window.handle_command_response(success, message)

    def destroy_node(self):
        if hasattr(self, "keyword_listener") and self.keyword_listener is not None:
            self.keyword_listener.stop()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)

    app = QApplication(sys.argv)
    window = AssemblyUI()
    node = UINode(window)

    # PyQt 이벤트 루프 안에서 ROS 콜백도 조금씩 처리한다.
    # 음성 인식은 별도 thread에서 돌고, UI 갱신은 Qt signal로 처리한다.
    ros_timer = QTimer()
    ros_timer.timeout.connect(lambda: rclpy.spin_once(node, timeout_sec=0.0))
    ros_timer.start(30)

    window.show()

    try:
        exit_code = app.exec()
    finally:
        ros_timer.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
