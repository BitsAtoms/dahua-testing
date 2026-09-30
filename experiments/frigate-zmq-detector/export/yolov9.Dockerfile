# YOLOv9 ONNX export for Frigate's yolo-generic model type.
# Frigate 0.17 documented recipe, pinned to an upstream commit. Frigate's float
# input (RGB / 255) is what YOLOv9 expects, so no graph change is needed.
#
#   docker build experiments/frigate-zmq-detector/export -f experiments/frigate-zmq-detector/export/yolov9.Dockerfile \
#     --build-arg MODEL_SIZE=m --build-arg IMG_SIZE=320 \
#     --output experiments/frigate-zmq-detector/models
FROM python:3.11 AS build
RUN apt-get update && apt-get install --no-install-recommends -y cmake git libgl1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/
WORKDIR /yolov9
ARG YOLOV9_COMMIT=5b1ea9a8b3f0ffe4fe0e203ec6232d788bb3fcff
RUN git clone https://github.com/WongKinYiu/yolov9.git . && git checkout ${YOLOV9_COMMIT}
RUN uv pip install --system -r requirements.txt
# torch >= 2.9 exports with the dynamo exporter and forces ONNX opset 18, which
# DirectML 1.24 rejects (E_INVALIDARG). torch 2.8.0 keeps the classic exporter.
RUN uv pip install --system --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 torchvision==0.23.0
RUN uv pip install --system onnx==1.18.0 onnxruntime onnx-simplifier==0.4.* onnxscript
ARG MODEL_SIZE
ARG IMG_SIZE
ADD https://github.com/WongKinYiu/yolov9/releases/download/v0.1/yolov9-${MODEL_SIZE}-converted.pt yolov9-${MODEL_SIZE}.pt
RUN sha256sum yolov9-${MODEL_SIZE}.pt
RUN sed -i "s/ckpt = torch.load(attempt_download(w), map_location='cpu')/ckpt = torch.load(attempt_download(w), map_location='cpu', weights_only=False)/g" models/experimental.py
RUN python3 export.py --weights ./yolov9-${MODEL_SIZE}.pt --imgsz ${IMG_SIZE} --simplify --include onnx
FROM scratch
ARG MODEL_SIZE
ARG IMG_SIZE
COPY --from=build /yolov9/yolov9-${MODEL_SIZE}.onnx /yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx
