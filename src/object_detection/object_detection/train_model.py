#!/usr/bin/env python3
"""Train a custom YOLO segmentation model for hub/part recognition.

The collected raw images must first be polygon-labeled and split into:

    dataset/images/train
    dataset/images/val
    dataset/labels/train
    dataset/labels/val

Labels must use the Ultralytics YOLO segmentation text format.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Iterable


def _default_package_root() -> Path:
    return Path.home() / "ros2_ws" / "src" / "object_detection"


def _count_files(directory: Path, suffixes: Iterable[str]) -> int:
    suffix_set = {suffix.lower() for suffix in suffixes}
    return sum(
        1
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in suffix_set
    )


def _write_dataset_yaml(path: Path, dataset_root: Path) -> None:
    """Create a two-class dataset YAML when one does not exist."""
    content = (
        f"path: {dataset_root.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: hub\n"
        "  1: part1\n"
        "  2: part2\n"
        "  3: part3\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _validate_dataset(dataset_root: Path) -> None:
    required = [
        dataset_root / "images" / "train",
        dataset_root / "images" / "val",
        dataset_root / "labels" / "train",
        dataset_root / "labels" / "val",
    ]
    missing = [path for path in required if not path.is_dir()]
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(
            "The labeled dataset is incomplete. Missing directories:\n"
            f"{formatted}\n"
            "Collecting raw JPG files alone is not enough; polygon labels are required."
        )

    image_suffixes = (".jpg", ".jpeg", ".png", ".bmp")
    train_images = _count_files(required[0], image_suffixes)
    val_images = _count_files(required[1], image_suffixes)
    train_labels = _count_files(required[2], (".txt",))
    val_labels = _count_files(required[3], (".txt",))

    print("Dataset summary")
    print(f"  train images: {train_images}")
    print(f"  train labels: {train_labels}")
    print(f"  val images:   {val_images}")
    print(f"  val labels:   {val_labels}")

    if train_images == 0 or val_images == 0:
        raise RuntimeError("Both train and val image folders must contain images.")
    if train_labels == 0 or val_labels == 0:
        raise RuntimeError("Both train and val label folders must contain labels.")


def build_parser() -> argparse.ArgumentParser:
    package_root = _default_package_root()
    dataset_root = package_root / "dataset"

    parser = argparse.ArgumentParser(
        description="Train a hub/part YOLO instance-segmentation model."
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=dataset_root,
        help="Dataset root containing images/ and labels/.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Dataset YAML. Defaults to <dataset-root>/hub_part.yaml.",
    )
    parser.add_argument(
        "--model",
        default="yolov8n-seg.pt",
        help="Ultralytics segmentation checkpoint used for transfer learning.",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--device",
        default=None,
        help="Examples: 0, 0,1, cpu. Omit for Ultralytics automatic selection.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=package_root / "runs" / "segment",
    )
    parser.add_argument("--name", default="hub_part_seg")
    parser.add_argument(
        "--output-model",
        type=Path,
        default=package_root / "resource" / "hub_part_seg.pt",
        help="Where the final best.pt is copied for the ROS2 vision node.",
    )
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    dataset_root = args.dataset_root.expanduser().resolve()
    data_yaml = (
        args.data.expanduser().resolve()
        if args.data is not None
        else dataset_root / "hub_part.yaml"
    )

    if not data_yaml.exists():
        _write_dataset_yaml(data_yaml, dataset_root)
        print(f"Created dataset YAML: {data_yaml}")

    _validate_dataset(dataset_root)

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "Ultralytics is not installed. Run: pip install ultralytics"
        ) from exc

    model = YOLO(args.model)
    train_kwargs = {
        "data": str(data_yaml),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "workers": args.workers,
        "project": str(args.project.expanduser()),
        "name": args.name,
        "exist_ok": True,
    }
    if args.device not in (None, ""):
        train_kwargs["device"] = args.device

    results = model.train(**train_kwargs)

    save_dir = Path(str(results.save_dir))
    best_model = save_dir / "weights" / "best.pt"
    if not best_model.is_file():
        raise FileNotFoundError(f"Training finished but best.pt was not found: {best_model}")

    output_model = args.output_model.expanduser().resolve()
    output_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_model, output_model)

    class_json = output_model.parent / "class_names_hub_part.json"
    class_json.write_text(
        json.dumps(
    {
        "0": "hub",
        "1": "part1",
        "2": "part2",
        "3": "part3",
    },
    ensure_ascii=False,
    indent=2,
),
        encoding="utf-8",
    )

    print("\nTraining complete")
    print(f"  Ultralytics run: {save_dir}")
    print(f"  ROS2 model copy: {output_model}")
    print(f"  Class names:     {class_json}")
    print("Rebuild the package so the new resource file is copied into install/:")
    print("  cd ~/ros2_ws && colcon build --packages-select object_detection")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
