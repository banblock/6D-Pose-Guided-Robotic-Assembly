import cv2
import numpy as np
from pathlib import Path


def project_points(points_3d, T, K):
    R = T[:3, :3]
    t = T[:3, 3]

    points_cam = (R @ points_3d.T).T + t

    points_2d = []
    for x, y, z in points_cam:
        if z <= 0:
            raise ValueError(f"Invalid z: {z}")

        u = K[0, 0] * x / z + K[0, 2]
        v = K[1, 1] * y / z + K[1, 2]

        points_2d.append((int(round(u)), int(round(v))))

    return points_2d


def draw_pose_axis(image, T, K, axis_length=0.05):
    points_3d = np.array([
        [0.0, 0.0, 0.0],
        [axis_length, 0.0, 0.0],
        [0.0, axis_length, 0.0],
        [0.0, 0.0, axis_length],
    ], dtype=np.float64)

    origin, x_axis, y_axis, z_axis = project_points(points_3d, T, K)

    result = image.copy()

    cv2.arrowedLine(result, origin, x_axis, (0, 0, 255), 3)
    cv2.arrowedLine(result, origin, y_axis, (0, 255, 0), 3)
    cv2.arrowedLine(result, origin, z_axis, (255, 0, 0), 3)

    cv2.putText(result, "X", x_axis, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    cv2.putText(result, "Y", y_axis, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.putText(result, "Z", z_axis, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)

    return result


def main():
    scene_dir = Path("demo_data/hub0")
    debug_dir = Path("debug_hub")

    rgb_path = scene_dir / "rgb" / "000000.png"
    k_path = scene_dir / "cam_K.txt"
    pose_path = debug_dir / "ob_in_cam" / "000000.txt"

    image = cv2.imread(str(rgb_path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Cannot read image: {rgb_path}")

    K = np.loadtxt(k_path).reshape(3, 3)
    T_camera_hub = np.loadtxt(pose_path).reshape(4, 4)

    result = draw_pose_axis(
        image=image,
        T=T_camera_hub,
        K=K,
        axis_length=0.05,
    )

    out_path = Path("result/manual_pose_axis.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), result)

    print("Saved:", out_path)
    print("T_camera_hub:")
    print(T_camera_hub)


if __name__ == "__main__":
    main()