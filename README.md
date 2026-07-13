# 6D-Pose-Guided-Robotic-Assembly

## 프로젝트 소개

본 프로젝트는 '별도의 학습없이 새로운 객체에 대응 가능한 유연한 로봇제어 시스템'을 목표로 기존의 학습을 통한 AI제어의 한계를 해결할 방안을 탐색하고 목표 달성에 보다 효용성있는 수단을 실험하는 것을 지향한다.
본 프로젝트의 메인 테스크는 음성 명령과 RGB-D 비전을 활용한 협동로봇 자동 조립 시스템이다. 사용자가 조립할 면을 음성으로 지정하면, YOLO Segmentation을 통해 허브와 부품을 인식하고 FoundationPose를 이용해 허브의 6D Pose를 추정한다. 추정 결과는 Hand-eye Calibration 정보를 바탕으로 그리퍼 기준 상대 좌표로 변환되며, 협동로봇은 이를 이용해 고정된 부품을 지정된 면에 조립한다.
결과적으로 부품허브는 사용자가 원하는 6D Pose로 정렬되어 예측의 정확도를 증명한다. 
프로젝트에서 사용한 6D Pose 모델은 어떠한 추가 학습과정도 거치지 않았으며 Pose 예측에 있어 모델 입력에 필요한 물체의 mesh 및 texture 데이터 이외의 고정리소스는 사용하지 않는다.

### 주요 기능

- Wake Word 기반 음성 명령 입력
- STT 및 LLM을 이용한 조립 면 추출
- RGB-D 영상 기반 허브·부품 Segmentation
- FoundationPose 기반 허브 6D Pose 추정
- Hand-eye 변환을 이용한 그리퍼 기준 좌표 계산
- ROS 2 서비스 기반 조립 작업 시퀀스 실행

## 프로젝트 목표

- depth 카메라를 통한 객체 인식 및 객체의 6D Pose를 별도의 학습 없이 AI를 통해 추정한다.
- 허브의 위치와 자세 변화에 대응할 수 있는 자동 조립 시스템을 구현한다.
- 음성 명령, 객체 인식, 6D Pose 추정, 로봇 동작을 하나의 ROS 2 기반 파이프라인으로 연결한다.
- 카메라 기준으로 추정한 허브 Pose를 그리퍼 기준 상대 좌표로 변환해 실제 로봇 제어에 적용한다.

## 전체 동작 흐름

1. 사용자가 UI에서 음성 인식을 시작한다.
2. Wake Word 감지 후 사용자의 음성을 STT로 변환한다.
3. LLM을 이용해 음성 명령에서 조립할 면 정보를 추출한다.
4. 사용자가 UI에서 인식 결과를 확인하면 `/assembly/command` 서비스를 호출한다.
5. YOLO Segmentation으로 허브와 부품을 인식하고 대상 객체의 Mask를 생성한다.
6. FoundationPose가 RGB, Depth, Mask, 카메라 내부 파라미터와 CAD 모델을 이용해 허브의 6D Pose를 추정한다.
7. 추정된 Pose를 Hand-eye Calibration 결과를 이용해 그리퍼 기준 상대 좌표로 변환한다.
8. 로봇이 허브 파지, 기준 위치 배치, 부품 파지, 지정 면 조립 순서로 작업을 수행한다.

## 시스템 구성

| 구성 요소 | 역할 |
|---|---|
| `voice_processing` | PyQt UI, Wake Word 감지, STT 및 LLM 기반 음성 명령 처리 |
| `assembly_controller` | 조립 명령 검증과 전체 작업 흐름 관리 |
| `object_detection` | RGB-D 영상 처리, YOLO Segmentation 및 Mask 생성 |
| `foundationpose_client` | 비전 데이터 요청, FoundationPose 서버 추론 요청 및 좌표 변환 |
| `foundation_server` | RGB·Depth·Mask와 CAD 모델을 이용한 허브 6D Pose 추정을 수행하는 FastAPI server |
| `robot_assembly` | 로봇 작업 시퀀스 생성 및 Doosan DRL 실행 요청 |
| `interfaces` | 노드 간 통신에 사용하는 ROS 2 서비스 인터페이스 정의 |

## 주요 기술

- ROS 2 Humble
- Intel RealSense RGB-D Camera
- YOLOv8 Segmentation
- FoundationPose
- OpenAI STT 및 LLM
- PyQt6
- Doosan M0609 협동로봇
- Docker
- FastAPI

# 설치 및 실행 메뉴얼

## 프로젝트 다운로드 및 워크스페이스 구성

본 프로젝트 저장소에는 ROS 2 패키지와 수정된 FoundationPose 서버 코드가 모두 포함되어 있다.

따라서 NVlabs의 FoundationPose 원본 저장소를 별도로 clone하지 않는다.

```bash
cd ~

git clone \
    https://github.com/banblock/6D-Pose-Guided-Robotic-Assembly.git

cd ~/6D-Pose-Guided-Robotic-Assembly
```

프로젝트 구조는 다음과 같다.

```text
6D-Pose-Guided-Robotic-Assembly
├── foundation_server
│   └── FoundationPose
│       ├── docker
│       ├── weights
│       ├── resource
│       ├── foundationpose_server.py
│       ├── run_server.sh
│       └── build_all.sh
│
└── src
    ├── assembly_controller
    ├── foundationpose_client
    ├── interfaces
    ├── object_detection
    ├── robot_assembly
    └── voice_processing
```

이후 설명에서는 프로젝트 루트 경로를 다음과 같이 사용한다.

```bash
export PROJECT_ROOT=~/6D-Pose-Guided-Robotic-Assembly
```

환경변수를 계속 사용하려면 `.bashrc`에 등록한다.

```bash
echo \
'export PROJECT_ROOT=~/6D-Pose-Guided-Robotic-Assembly' \
>> ~/.bashrc

source ~/.bashrc
```

---

# FoundationPose 가중치 설치

## 사전학습 가중치 다운로드

프로젝트에는 FoundationPose 서버 코드가 포함되어 있지만, 용량이 큰 사전학습 가중치는 Git 저장소에 포함되어 있지 않다.

FoundationPose 공식 저장소의 `Data prepare` 항목에서 네트워크 가중치를 다운로드한다.

* 공식 저장소:
  `https://github.com/NVlabs/FoundationPose`
* Refiner 모델:
  `2023-10-28-18-33-37`
* Scorer 모델:
  `2024-01-11-20-02-45`

다운로드한 두 디렉터리를 다음 경로에 배치한다.

```text
foundation_server/FoundationPose/weights/
├── 2023-10-28-18-33-37
│   └── model_best.pth
└── 2024-01-11-20-02-45
    └── model_best.pth
```

가중치 디렉터리를 먼저 생성한다.

```bash
mkdir -p \
    "$PROJECT_ROOT/foundation_server/FoundationPose/weights"
```

다운로드한 압축파일이 `~/Downloads`에 있다고 가정하면 압축을 해제한다.

```bash
cd "$PROJECT_ROOT/foundation_server/FoundationPose/weights"

unzip ~/Downloads/2023-10-28-18-33-37.zip
unzip ~/Downloads/2024-01-11-20-02-45.zip
```

다운로드 형식에 따라 압축파일 이름은 다를 수 있다.

가중치가 올바르게 배치되었는지 확인한다.

```bash
find \
    "$PROJECT_ROOT/foundation_server/FoundationPose/weights" \
    -name "model_best.pth" \
    -type f
```

정상적인 경우 다음 두 파일이 출력되어야 한다.

```text
weights/2023-10-28-18-33-37/model_best.pth
weights/2024-01-11-20-02-45/model_best.pth
```

> FoundationPose 데모 데이터와 대규모 학습 데이터는 프로젝트 서버 실행에 필요하지 않다.
> 현재 프로젝트에서는 Refiner와 Scorer 가중치만 설치한다.

---

# FoundationPose Docker 환경 설치

## Docker 이미지 다운로드

프로젝트에 포함된 FoundationPose 디렉터리로 이동한다.

```bash
cd "$PROJECT_ROOT/foundation_server/FoundationPose"
```

FoundationPose 공식 Docker 이미지를 다운로드한다.

```bash
docker pull wenbowen123/foundationpose
```

프로젝트의 Docker 스크립트에서 사용하는 이름으로 태그한다.

```bash
docker tag \
    wenbowen123/foundationpose \
    foundationpose
```

---

## FoundationPose 컨테이너 생성

프로젝트에 포함된 Docker 실행 스크립트를 사용한다.

```bash
cd "$PROJECT_ROOT/foundation_server/FoundationPose"

bash docker/run_container.sh
```

컨테이너 이름은 일반적으로 다음과 같다.

```text
foundationpose
```

생성된 컨테이너를 확인한다.

```bash
docker ps -a | grep foundationpose
```

### 볼륨 경로 확인

Docker 실행 스크립트가 현재 프로젝트 경로를 컨테이너에 마운트하는지 확인한다.

```bash
cat \
    "$PROJECT_ROOT/foundation_server/FoundationPose/docker/run_container.sh"
```

스크립트에 과거 개발 환경의 절대경로가 들어 있다면 다음 항목을 현재 경로로 수정해야 한다.

```text
~/6D-Pose-Guided-Robotic-Assembly/foundation_server/FoundationPose
```

권장 마운트 예시는 다음과 같다.

```bash
-v "$PROJECT_ROOT/foundation_server/FoundationPose:/home/FoundationPose"
```

컨테이너 내부 경로는 실제 `run_container.sh` 설정에 따라 달라질 수 있다.

---

## FoundationPose 확장 모듈 최초 빌드

컨테이너 생성 직후 FoundationPose 컨테이너 내부에서 한 번만 실행한다.

```bash
docker exec -it foundationpose bash
```

컨테이너 내부에서 FoundationPose 경로로 이동한다.

```bash
cd /home/FoundationPose
```

마운트 경로가 다른 경우 다음 명령으로 찾는다.

```bash
find / -name "build_all.sh" 2>/dev/null
```

확인된 FoundationPose 디렉터리로 이동한 후 빌드한다.

```bash
bash build_all.sh
```

환경을 확인한다.

```bash
python check_env.py
```

---

# FoundationPose 서버 실행

## run_server.sh 실행

FoundationPose 컨테이너에 접속한다.

```bash
docker start foundationpose

docker exec -it foundationpose bash
```

컨테이너 내부에서 프로젝트의 FoundationPose 서버 디렉터리로 이동한다.

```bash
cd /home/FoundationPose
```

서버 스크립트에 실행 권한을 부여한다.

```bash
chmod +x run_server.sh
```

서버를 실행한다.

```bash
./run_server.sh
```

서버 기본 주소는 다음과 같다.

```text
http://127.0.0.1:8000
```

다른 Host 터미널에서 서버 상태를 확인한다.

```bash
curl http://127.0.0.1:8000/health
```

FoundationPose 모델을 메모리에서 해제하려면 다음 명령을 사용한다.

```bash
curl -X POST http://127.0.0.1:8000/unload
```

---

# ROS 2 프로젝트 빌드

프로젝트 저장소 자체를 ROS 2 워크스페이스로 사용한다.

```bash
cd "$PROJECT_ROOT"

source /opt/ros/humble/setup.bash
```

Doosan ROS 2 패키지가 별도 워크스페이스에 설치되어 있다면 먼저 source한다.

```bash
source ~/doosan_ws/install/setup.bash
```

의존성을 설치한다.

```bash
rosdep install \
    --from-paths src \
    --ignore-src \
    -r \
    -y \
    --rosdistro humble
```

Host에서 실행할 패키지를 빌드한다.

```bash
colcon build \
    --symlink-install \
    --packages-skip object_detection
```

빌드 결과를 적용한다.

```bash
source "$PROJECT_ROOT/install/setup.bash"
```

---

# 프로젝트 실행 순서

프로젝트는 다음 순서로 실행한다.

1. FoundationPose Server
2. RealSense Camera
3. AI Vision
4. Doosan Bringup
5. Robot Arm Node
6. FoundationPose Client
7. Assembly Controller
8. Voice Processing UI

각 프로세스는 별도의 터미널에서 실행한다.

---

## 터미널 1 — FoundationPose Server

```bash
docker start foundationpose

docker exec -it foundationpose bash
```

컨테이너 내부:

```bash
cd /home/FoundationPose

./run_server.sh
```

Host에서 상태 확인:

```bash
curl http://127.0.0.1:8000/health
```

---

## 터미널 2 — RealSense Camera

```bash
source /opt/ros/humble/setup.bash
source "$PROJECT_ROOT/install/setup.bash"

export ROS_DOMAIN_ID=99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

RealSense 노드를 실행한다.

```bash
ros2 launch realsense2_camera rs_align_depth_launch.py \
    depth_module.depth_profile:=848x480x30 \
    rgb_camera.color_profile:=1280x720x30 \
    initial_reset:=true \
    align_depth.enable:=true \
    enable_rgbd:=true \
    pointcloud.enable:=true
```

주요 카메라 토픽을 확인한다.

```bash
ros2 topic list | grep camera
```

프로젝트에서 사용하는 주요 토픽은 다음과 같다.

```text
/camera/camera/color/image_raw
/camera/camera/aligned_depth_to_color/image_raw
```

---

## 터미널 3 — AI Vision

Host에서 Object Detection 컨테이너를 실행한다.

```bash
docker start object_detection

docker exec -it object_detection bash
```

컨테이너 내부:

```bash
source /opt/ros/humble/setup.bash
source /home/ros2_ws/install/setup.bash

export ROS_DOMAIN_ID=99 #본인 네트워크에 맞는 ID로 설정한다.
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

ros2 run object_detection ai_vision
```

서비스를 확인한다.

```bash
ros2 service list | grep ai_vision
```

정상적인 경우 다음 서비스가 표시된다.

```text
/ai_vision/get_vision_data
```

---

## 터미널 4 — Doosan Robot Bringup

Host 터미널에서 ROS 2 환경을 적용한다.

```bash
source /opt/ros/humble/setup.bash
source ~/doosan_ws/install/setup.bash
source "$PROJECT_ROOT/install/setup.bash"

export ROS_DOMAIN_ID=99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

실제 Doosan M0609 로봇에 연결한다.

```bash
ros2 launch dsr_bringup2 dsr_bringup2_rviz.launch.py \
    mode:=real \
    host:=192.168.1.100 \
    port:=12345 \
    model:=m0609
```

Doosan Controller와 Host PC가 같은 네트워크 대역에 있어야 한다.

연결 상태를 확인한다.

```bash
ping 192.168.1.100
```

Doosan 관련 노드를 확인한다.

```bash
ros2 node list | grep dsr
```

---

## 터미널 5 — Robot Arm Node

```bash
source /opt/ros/humble/setup.bash
source ~/doosan_ws/install/setup.bash
source "$PROJECT_ROOT/install/setup.bash"

export ROS_DOMAIN_ID=99 #본인 네트워크에 맞는 ID로 설정한다.
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

ros2 run robot_assembly robot_arm_node
```

서비스를 확인한다.

```bash
ros2 service list | grep /robot
```

---

## 터미널 6 — FoundationPose Client

```bash
source /opt/ros/humble/setup.bash
source "$PROJECT_ROOT/install/setup.bash"

export ROS_DOMAIN_ID=99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

ros2 run foundationpose_client foundationpose_client_node
```

FoundationPose 서버 주소를 명시하려면 다음과 같이 실행한다.

```bash
ros2 run foundationpose_client foundationpose_client_node \
    --ros-args \
    -p server_base_url:=http://127.0.0.1:8000
```

서비스를 확인한다.

```bash
ros2 service list | grep foundationpose
```

정상적인 경우 다음 서비스가 표시된다.

```text
/foundationpose/estimate_pose
```

---

## 터미널 7 — Assembly Controller

```bash
source /opt/ros/humble/setup.bash
source "$PROJECT_ROOT/install/setup.bash"

export ROS_DOMAIN_ID=99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

ros2 run assembly_controller controller
```

서비스를 확인한다.

```bash
ros2 service list | grep assembly
```

정상적인 경우 다음 서비스가 표시된다.

```text
/assembly/command
```

---

## 터미널 8 — Voice Processing UI

```bash
source /opt/ros/humble/setup.bash
source "$PROJECT_ROOT/install/setup.bash"

export ROS_DOMAIN_ID=99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

ros2 run voice_processing ui
```

---

# UI 사용 방법

모든 노드가 정상적으로 실행되면 다음 순서로 조작한다.

1. UI에서 `시작` 버튼을 누른다.
2. Wake Word를 말한다.

```text
Hello Rokey
```

한국어 발음:

```text
헬로 로키
```

3. Wake Word 인식 후 원하는 조립 면을 말한다.

```text
A면 조립해줘
```

```text
B면 조립해줘
```

```text
C면 조립해줘
```

지원하는 조립 면은 A, B, C의 세 면이다.

| 음성 명령   | 전달되는 면 번호 |
| ------- | --------: |
| A면 조립해줘 |         1 |
| B면 조립해줘 |         2 |
| C면 조립해줘 |         3 |

---

# 전체 실행 명령 요약

## FoundationPose Server

```bash
docker start foundationpose
docker exec -it foundationpose bash
```

컨테이너 내부:

```bash
cd /home/FoundationPose
./run_server.sh
```

## RealSense

```bash
ros2 launch realsense2_camera rs_align_depth_launch.py \
    depth_module.depth_profile:=848x480x30 \
    rgb_camera.color_profile:=1280x720x30 \
    initial_reset:=true \
    align_depth.enable:=true \
    enable_rgbd:=true \
    pointcloud.enable:=true
```

## AI Vision

```bash
ros2 run object_detection ai_vision
```

## Doosan Bringup

```bash
ros2 launch dsr_bringup2 dsr_bringup2_rviz.launch.py \
    mode:=real \
    host:=192.168.1.100 \
    port:=12345 \
    model:=m0609
```

## Robot Arm

```bash
ros2 run robot_assembly robot_arm_node
```

## FoundationPose Client

```bash
ros2 run foundationpose_client foundationpose_client_node
```

## Assembly Controller

```bash
ros2 run assembly_controller controller
```

## Voice UI

```bash
ros2 run voice_processing ui
```
