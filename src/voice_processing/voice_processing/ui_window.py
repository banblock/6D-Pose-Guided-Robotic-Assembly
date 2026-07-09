from datetime import datetime
from pathlib import Path
import re

from PyQt6 import uic
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMainWindow


class UISignals(QObject):
    """UI 내부 이벤트용 Qt signal."""

    # 선택 버튼을 눌렀을 때 ui_node.py로 보낼 확정 명령
    command_confirmed = pyqtSignal(int, int)  # part_id, face_id

    # 시작 화면에서 명령 화면으로 넘어갔을 때 음성 인식을 시작/재개하도록 요청
    voice_start_requested = pyqtSignal()
    voice_resume_requested = pyqtSignal()


class AssemblyUI(QMainWindow):
    """Qt Designer로 만든 .ui 파일을 불러와 화면 동작만 담당하는 클래스."""

    def __init__(self):
        super().__init__()

        ui_path = Path(__file__).with_name("assembly_command_ui.ui")
        uic.loadUi(str(ui_path), self)

        self.signals = UISignals()

        self.current_part = ""
        self.current_face = ""
        self.current_voice_text = ""

        self.startButton.clicked.connect(self.go_to_command_page)
        self.selectButton.clicked.connect(self.on_select_clicked)
        self.cancelButton.clicked.connect(self.on_cancel_clicked)

        self.reset_command_area(clear_voice=False)
        self.set_status_waiting()

        # 첫 화면에서는 아직 음성 인식을 시작하지 않는다.
        # 사용자가 시작 버튼을 눌러 2번 화면으로 넘어간 뒤에만 Hello Rokey 감지를 시작한다.
        self.stackedWidget.setCurrentIndex(0)

        # 테스트용 예시. 필요하면 주석 해제해서 UI만 확인할 수 있음.
        # self.stackedWidget.setCurrentIndex(1)
        # self.set_voice_command("Hello, Rokey. A면에 3번 부품 조립")
        # self.set_command("3", "A")

    def go_to_command_page(self):
        self.stackedWidget.setCurrentIndex(1)
        self.set_voice_waiting()
        self.signals.voice_start_requested.emit()

    def set_command_buttons_enabled(self, enabled: bool):
        self.selectButton.setEnabled(enabled)
        self.cancelButton.setEnabled(enabled)

    def set_command_pending(self):
        """서비스 응답 대기 중: 중복 확인만 막고 취소는 유지한다."""
        self.selectButton.setEnabled(False)
        self.cancelButton.setEnabled(True)

    def set_command_working(self):
        """서비스 통신 성공 후 작업 진행 중: 확인은 막고 취소는 유지한다."""
        self.selectButton.setEnabled(False)
        self.cancelButton.setEnabled(True)

    def set_status_waiting(self):
        """오른쪽 작업 상태 영역은 실제 manager/robot 기록용으로 최소 표시만 유지."""
        self.statusText.clear()
        self.statusText.append("작업 기록 대기 중")

    def set_voice_waiting(self):
        """왼쪽 음성 인식 영역: wakeup word 대기 안내."""
        self.voiceText.setPlainText("Hello Rokey라고 말해보세요.")
        self.stackedWidget.setCurrentIndex(1)

    def set_voice_recording(self):
        """왼쪽 음성 인식 영역: STT 녹음 중 안내."""
        self.voiceText.setPlainText(
            "명령을 내려주세요.\n"
            "5초간 인식합니다."
        )
        self.stackedWidget.setCurrentIndex(1)

    def set_voice_unrecognized(self, detail: str = ""):
        """왼쪽 음성 인식 영역: 명령 인식 실패 안내."""
        message = "명령을 인식하지 못했습니다."
        detail = str(detail).strip()
        if detail:
            message += f"\n{detail}"
        self.voiceText.setPlainText(message)
        self.stackedWidget.setCurrentIndex(1)

    def set_voice_state(self, state: str, detail: str = ""):
        """ui_node.py의 음성 처리 상태를 왼쪽 음성 인식 영역에 표시."""
        state = str(state).strip().lower()
        detail = str(detail).strip()

        if state == "waiting":
            self.set_voice_waiting()
        elif state == "recording":
            self.set_voice_recording()
        elif state == "unrecognized":
            self.set_voice_unrecognized(detail)
        else:
            self.voiceText.setPlainText(detail or state)
            self.stackedWidget.setCurrentIndex(1)

    def reset_command_area(self, clear_voice: bool = False):
        self.current_part = ""
        self.current_face = ""
        self.commandLabel.setText("부품: ____   /   면: ____")
        self.set_command_buttons_enabled(False)

        if clear_voice:
            self.current_voice_text = ""
            self.set_voice_waiting()

    def set_voice_command(self, text: str):
        """UI 내부 음성 인식 결과 원문을 왼쪽 하단에 표시."""
        self.current_voice_text = str(text).strip()
        if self.current_voice_text:
            self.voiceText.setPlainText(
                "입력 받은 명령\n"
                f"{self.current_voice_text}"
            )
        else:
            self.set_voice_unrecognized()

        self.stackedWidget.setCurrentIndex(1)

    def set_command(self, part: str, face: str):
        """UI 내부 음성 파싱 결과를 화면에 표시.

        예:
            self.set_command("3", "A")
            self.set_command("부품3", "A면")
        """
        self.current_part = str(part).strip()
        self.current_face = str(face).strip()

        part_text = self.current_part if self.current_part else "____"
        face_text = self.current_face if self.current_face else "____"
        self.commandLabel.setText(f"부품: {part_text}   /   면: {face_text}")

        has_command = bool(self.current_part and self.current_face)
        self.set_command_buttons_enabled(has_command)

        self.stackedWidget.setCurrentIndex(1)
        if not has_command:
            self.set_voice_unrecognized("부품과 면을 모두 말해주세요.")

    def on_select_clicked(self):
        part_id = self.part_to_id(self.current_part)
        face_id = self.face_to_id(self.current_face)

        if part_id is None or face_id is None:
            self.add_status_log(
                f"명령 변환 실패: 부품={self.current_part or '미지정'}, 면={self.current_face or '미지정'}"
            )
            return

        # 여기서는 아직 작업 시작이 아니다.
        # manager_node 서비스 응답이 성공으로 돌아온 뒤 handle_command_response()에서 작업 시작을 표시한다.
        self.signals.command_confirmed.emit(part_id, face_id)

    def on_cancel_clicked(self):
        self.reset_command_area(clear_voice=True)
        self.add_status_log("명령 취소")
        self.signals.voice_resume_requested.emit()

    def handle_command_response(self, success: bool, message: str):
        """manager_node의 AssemblyCommand 서비스 응답을 UI에 반영.

        주의:
            이 응답은 '작업 완료'가 아니라 manager_node가 명령을 접수했는지에 대한 응답이다.
            따라서 성공 응답을 받더라도 음성 인식은 다시 켜지지 않는다.
            작업 종료 시점에 다시 켜려면 mark_task_finished()를 호출하면 된다.
        """
        if success:
            # 서비스 통신이 성공한 시점부터 실제 작업 시작으로 표시한다.
            self.add_status_log("작업 시작")
            self.set_command_working()
        else:
            self.add_status_log(message or "조립 명령 전송 실패")
            # 실패 시 사용자가 다시 선택하거나 취소할 수 있게 버튼은 유지한다.
            self.set_command_buttons_enabled(True)

    def mark_task_finished(self, message: str = "작업 종료"):
        """나중에 manager_node의 작업 완료 신호가 생기면 호출할 함수."""
        self.add_status_log(message)
        self.reset_command_area(clear_voice=True)
        self.signals.voice_resume_requested.emit()

    def add_status_log(self, text: str):
        """오른쪽 하단 작업 상태 영역에 시간 포함 로그를 누적 표시.

        이 영역은 음성 인식 진행 상태가 아니라 manager_node/robot 쪽 작업 기록용이다.
        """
        now = datetime.now().strftime("%H:%M:%S")
        message = str(text).strip()
        if not message:
            return

        for line in message.splitlines():
            self.statusText.append(f"[{now}] {line}")

        scrollbar = self.statusText.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def part_to_id(part):
        """부품 문자열을 manager_node로 보낼 int8 id로 변환."""
        text = str(part).strip().lower()
        if not text:
            return None

        # "3", "부품3", "3번", "part3" 모두 3으로 처리
        match = re.search(r"[1-3]", text)
        if not match:
            return None
        return int(match.group())

    @staticmethod
    def face_to_id(face):
        """면 문자열을 manager_node로 보낼 int8 id로 변환.

        A면=1, B면=2, C면=3, D면=4
        """
        text = str(face).strip().upper()
        if not text:
            return None

        if text in {"1", "A", "A면"}:
            return 1
        if text in {"2", "B", "B면"}:
            return 2
        if text in {"3", "C", "C면"}:
            return 3
        if text in {"4", "D", "D면"}:
            return 4

        for ch in text:
            if ch in "ABCD":
                return "ABCD".index(ch) + 1

        return None
