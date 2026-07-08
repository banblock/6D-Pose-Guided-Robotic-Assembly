#!/usr/bin/env python3
import os
from datetime import datetime

import cv2


DEVICE_NUMBER = 6

SAVE_DIR = os.path.expanduser(
    "~/ros2_ws/src/object_detection/dataset/images/raw"
)


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    cap = cv2.VideoCapture(DEVICE_NUMBER)

    if not cap.isOpened():
        print(f"카메라를 열 수 없습니다. DEVICE_NUMBER={DEVICE_NUMBER}")
        return

    print(f"카메라 번호: {DEVICE_NUMBER}")
    print(f"저장 폴더: {SAVE_DIR}")
    print("s: 사진 저장 / q: 종료")

    while True:
        ret, frame = cap.read()

        if not ret:
            print("카메라 영상을 읽을 수 없습니다.")
            break

        cv2.imshow("camera", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            file_name = datetime.now().strftime(
                "%Y%m%d_%H%M%S_%f.jpg"
            )
            save_path = os.path.join(SAVE_DIR, file_name)

            if cv2.imwrite(save_path, frame):
                print(f"저장 완료: {save_path}")
            else:
                print(f"저장 실패: {save_path}")

        elif key == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
