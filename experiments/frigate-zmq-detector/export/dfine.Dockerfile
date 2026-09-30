# D-FINE ONNX export (Objects365 -> COCO weights) for Frigate's dfine model type.
# Frigate 0.17 documented recipe, pinned to an upstream commit and with the
# export size as a build argument (the recipe hard-codes 640). D-FINE's
# validation transform only scales RGB to [0, 1], which matches Frigate's float
# input, so no graph change is needed.
#
#   docker build experiments/frigate-zmq-detector/export -f experiments/frigate-zmq-detector/export/dfine.Dockerfile \
#     --build-arg MODEL_SIZE=m --build-arg IMG_SIZE=320 \
#     --output experiments/frigate-zmq-detector/models
FROM python:3.11 AS build
RUN apt-get update && apt-get install --no-install-recommends -y git libgl1 wget && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /bin/
WORKDIR /dfine
ARG DFINE_COMMIT=956d1709314c2c6a4df6f34de232054578a7449f
RUN git clone https://github.com/Peterande/D-FINE.git . && git checkout ${DFINE_COMMIT}
RUN uv pip install --system -r requirements.txt
# torch >= 2.9 exports with the dynamo exporter and forces ONNX opset 18, which
# DirectML 1.24 rejects (E_INVALIDARG). torch 2.8.0 keeps the classic exporter.
RUN uv pip install --system --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 torchvision==0.23.0
RUN uv pip install --system onnx onnxruntime onnxsim onnxscript
RUN mkdir -p output
ARG MODEL_SIZE
ARG IMG_SIZE
RUN wget -q https://github.com/Peterande/storage/releases/download/dfinev1.0/dfine_${MODEL_SIZE}_obj2coco.pth -O output/dfine_${MODEL_SIZE}_obj2coco.pth && sha256sum output/dfine_${MODEL_SIZE}_obj2coco.pth
# Batch size 1 at the requested size (line 58 of the pinned export script),
# and the matching precomputed positional embeddings and anchors, which the
# model builds for eval_spatial_size (640 in the shared include).
RUN sed -i "58s/data = torch.rand(.*)/data = torch.rand(1, 3, ${IMG_SIZE}, ${IMG_SIZE})/" tools/deployment/export_onnx.py && sed -n 58p tools/deployment/export_onnx.py
RUN sed -i "s/^eval_spatial_size: \[640, 640\]/eval_spatial_size: [${IMG_SIZE}, ${IMG_SIZE}]/" configs/dfine/include/dfine_hgnetv2.yml && grep -n "^eval_spatial_size" configs/dfine/include/dfine_hgnetv2.yml
# The checkpoint also stores the decoder anchor grid and valid mask for 640.
# They are not learned; keep the ones the model generated for IMG_SIZE.
RUN python3 -c "import pathlib; p = pathlib.Path('tools/deployment/export_onnx.py'); s = p.read_text(); old = '        cfg.model.load_state_dict(state)'; new = '        for key in (\"decoder.anchors\", \"decoder.valid_mask\"):\n            state[key] = cfg.model.state_dict()[key]\n' + old; assert s.count(old) == 1; p.write_text(s.replace(old, new))"
RUN python3 tools/deployment/export_onnx.py -c configs/dfine/objects365/dfine_hgnetv2_${MODEL_SIZE}_obj2coco.yml -r output/dfine_${MODEL_SIZE}_obj2coco.pth
FROM scratch
ARG MODEL_SIZE
ARG IMG_SIZE
COPY --from=build /dfine/output/dfine_${MODEL_SIZE}_obj2coco.onnx /dfine-${MODEL_SIZE}-${IMG_SIZE}.onnx
