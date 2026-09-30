#!/usr/bin/env python3
"""Measure the round trip of Frigate-style detection requests to a ZMQ client.

Sends the same multipart request Frigate 0.17 sends (JSON header + C-order
tensor) and reports latency percentiles. Run it on Windows and inside the
Frigate container against the same client to separate transport overhead from
inference. Only needs numpy and pyzmq, both present in the Frigate image:

  python zmq_roundtrip.py tcp://127.0.0.1:5556
  docker exec -i frigate python3 - tcp://host.docker.internal:5556 < zmq_roundtrip.py
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np
import zmq

endpoint = sys.argv[1]
runs = int(sys.argv[2]) if len(sys.argv) > 2 else 100
tensor = np.random.default_rng(20260930).random((1, 3, 320, 320), dtype=np.float32)
header = json.dumps({"shape": list(tensor.shape), "dtype": tensor.dtype.name, "model_type": "rfdetr"}).encode()

socket = zmq.Context().socket(zmq.REQ)
socket.setsockopt(zmq.RCVTIMEO, 5000)
socket.setsockopt(zmq.LINGER, 0)
socket.connect(endpoint)

latencies = []
for index in range(runs + 5):
    start = time.perf_counter()
    socket.send_multipart([header, tensor.tobytes(order="C")])
    reply = socket.recv_multipart()
    if index >= 5:
        latencies.append((time.perf_counter() - start) * 1000.0)
if len(reply) < 2 or len(reply[-1]) != 20 * 6 * 4:
    raise SystemExit(f"unexpected reply: {[len(part) for part in reply]}")
latencies.sort()
print(json.dumps({
    "endpoint": endpoint,
    "runs": runs,
    "request_bytes": tensor.nbytes,
    "p50_ms": round(latencies[len(latencies) // 2], 2),
    "p95_ms": round(latencies[int(len(latencies) * 0.95)], 2),
}))
