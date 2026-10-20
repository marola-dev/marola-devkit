#!/usr/bin/env python3
"""MIP-0085 §5.7 spike (throwaway): node vectors with model2vec 0.9.0 (the CI side; needs numpy).

    build_vectors.py MODEL_DIR NODES_JSON OUT_F32

MODEL_DIR holds config.json, tokenizer.json and model.safetensors only. OUT_F32 is the vectors,
float32 little-endian, one row per node in NODES_JSON's order.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    t0 = time.perf_counter()
    import numpy as np
    from model2vec import StaticModel

    model_dir, nodes, out = Path(argv[0]), Path(argv[1]), Path(argv[2])
    t1 = time.perf_counter()
    model = StaticModel.from_pretrained(str(model_dir))
    t2 = time.perf_counter()
    texts = [n["text"] for n in json.loads(nodes.read_text(encoding="utf-8"))]
    vecs = model.encode(texts).astype("<f4")
    t3 = time.perf_counter()
    out.write_bytes(np.ascontiguousarray(vecs).tobytes())
    print(
        f"{model_dir.name}: {len(texts)} x {vecs.shape[1]} -> {out.stat().st_size} B; "
        f"import {(t1 - t0) * 1000:.0f} ms, load {(t2 - t1) * 1000:.0f} ms, "
        f"encode {(t3 - t2) * 1000:.0f} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
