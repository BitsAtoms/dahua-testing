from pathlib import Path

import onnx
from onnx import TensorProto, helper, numpy_helper
import numpy as np


root = Path(__file__).resolve().parent
target = root / "models" / "matmul_relu.onnx"
target.parent.mkdir(parents=True, exist_ok=True)

identity = numpy_helper.from_array(np.eye(64, dtype=np.float32), name="weights")
bias = numpy_helper.from_array(np.full((64,), 0.25, dtype=np.float32), name="bias")
graph = helper.make_graph(
    [
        helper.make_node("MatMul", ["input", "weights"], ["product"]),
        helper.make_node("Add", ["product", "bias"], ["biased"]),
        helper.make_node("Relu", ["biased"], ["output"]),
    ],
    "windows-onnx-gpu-fixture",
    [helper.make_tensor_value_info("input", TensorProto.FLOAT, [64, 64])],
    [helper.make_tensor_value_info("output", TensorProto.FLOAT, [64, 64])],
    [identity, bias],
)
model = helper.make_model(graph, producer_name="dahua-windows-onnx-gpu", opset_imports=[helper.make_opsetid("", 20)])
model.ir_version = 10
onnx.checker.check_model(model)
onnx.save(model, target)
print(target)
