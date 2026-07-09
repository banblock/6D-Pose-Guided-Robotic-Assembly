#!/bin/bash
set -e

source /opt/conda/etc/profile.d/conda.sh
conda activate my

cd /home/hungeunlee/ws_cobot1_pjt/6D-Pose-Guided-Robotic-Assembly/foundation_server/FoundationPose

export PYTHONPATH=$PWD:$PYTHONPATH

export FP_MESH_FILE=$PWD/resource/hub.obj
export FP_DEBUG_DIR=$PWD/debug
export FP_DEBUG=0
export FP_EST_REFINE_ITER=1
export FP_TRACK_REFINE_ITER=1

export CUDA_VISIBLE_DEVICES=0
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export CUDA_MODULE_LOADING=LAZY
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:64,garbage_collection_threshold:0.6

python foundationpose_server.py