#!/usr/bin/env python3
"""MIP-0085 §5.7 spike (throwaway): right first, bytes and latency per candidate; stdlib only.

    bench.py DATA_DIR MODELS_DIR QUESTIONS_JSON            the table
    bench.py DATA_DIR MODELS_DIR --one METHOD "question"   one cold query (timed by the caller)

ROUTE_PINNED names a second route.py to score as the baseline "pinned".
DATA_DIR holds graph.json, nodes.json (nodetext.py) and vec-2M.f32 / vec-8M.f32 (build_vectors.py).
Order: nodetext.py, then build_vectors.py per model (a model2vec venv), parity.py, then this.
Right first is route bench's rule: an expected string in the first three NODE lines' files.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import time
import zlib
from collections import Counter
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "skills/routing/scripts"))
import route  # noqa: E402
from static_embed import StaticModel, cosine_rank, load_vectors  # noqa: E402

METHODS = ("pinned", "plain", "bm25", "potion-2M", "potion-8M")
# ROUTE_PINNED: the umbrella's vendored route.py (devkit c7ecce0, before T2), which scored the bundle.
PINNED = os.environ.get("ROUTE_PINNED", "")


class Plain:
    def __init__(self, data: Path, _models: Path):
        self.g = route.Graph(json.loads((data / "graph.json").read_text(encoding="utf-8")))

    def files(self, q: str) -> list[str]:
        out = route.render_query(self.g, q, 400).splitlines()
        return [
            ln.split("[src=", 1)[1].split(" loc=", 1)[0] for ln in out if ln.startswith("NODE ")
        ]


class Pinned(Plain):
    def __init__(self, data: Path, models: Path):
        spec = spec_from_file_location("route_pinned", PINNED)
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.route = mod
        self.g = mod.Graph(json.loads((data / "graph.json").read_text(encoding="utf-8")))

    def files(self, q: str) -> list[str]:
        out = self.route.render_query(self.g, q, 400).splitlines()
        return [
            ln.split("[src=", 1)[1].split(" loc=", 1)[0] for ln in out if ln.startswith("NODE ")
        ]


class BM25:
    K1, B = 1.2, 0.75

    def __init__(self, data: Path, _models: Path):
        nodes = json.loads((data / "nodes.json").read_text(encoding="utf-8"))
        self.file = [n["file"] for n in nodes]
        self.tf = [Counter(route.tokens(n["text"])) for n in nodes]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = sum(self.len) / len(self.len)
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(nodes)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def files(self, q: str) -> list[str]:
        raw = route.tokens(q)
        terms = list(dict.fromkeys(t for t in raw if t not in route.STOP)) or raw
        scored = []
        for i, c in enumerate(self.tf):
            s = 0.0
            norm = self.K1 * (1 - self.B + self.B * self.len[i] / self.avg)
            for t in terms:
                f = c.get(t)
                if f:
                    s += self.idf[t] * f * (self.K1 + 1) / (f + norm)
            if s > 0:
                scored.append((-s, i))
        scored.sort()
        return [self.file[i] for _, i in scored[:50]]


class Potion:
    size = ""

    def __init__(self, data: Path, models: Path):
        self.model = StaticModel(models / f"potion-base-{self.size}")
        nodes = json.loads((data / "nodes.json").read_text(encoding="utf-8"))
        self.file = [n["file"] for n in nodes]
        self.vecs = load_vectors(data / f"vec-{self.size}.f32", self.model.dim)

    def files(self, q: str) -> list[str]:
        return [self.file[i] for _, i in cosine_rank(self.model.encode(q), self.vecs)[:50]]


class Potion2M(Potion):
    size = "2M"


class Potion8M(Potion):
    size = "8M"


CLASSES = {
    "pinned": Pinned,
    "plain": Plain,
    "bm25": BM25,
    "potion-2M": Potion2M,
    "potion-8M": Potion8M,
}


def distinct(files: list[str]) -> list[str]:
    return list(dict.fromkeys(files))


def rank_of(files: list[str], expect: list[str]) -> int | None:
    for i, f in enumerate(files):
        if any(e in f for e in expect):
            return i + 1
    return None


def gz(path: Path) -> int:
    return len(zlib.compress(path.read_bytes(), 6))


def main(argv: list[str]) -> int:
    data, models = Path(argv[0]), Path(argv[1])
    if argv[2] == "--one":
        m = CLASSES[argv[3]](data, models)
        print("\n".join(m.files(argv[4])[:3]))
        return 0
    qs = json.loads(Path(argv[2]).read_text())["questions"]
    loaded, load_ms = {}, {}
    for name in METHODS:
        t0 = time.perf_counter()
        loaded[name] = CLASSES[name](data, models)
        load_ms[name] = (time.perf_counter() - t0) * 1000
    print("per question: rank of the first node line in an expected file (- = not in the first 50)")
    print("id   " + "".join(f"{m:>11}" for m in METHODS) + "  ask")
    right = {m: Counter() for m in METHODS}
    right_d = {m: Counter() for m in METHODS}
    warm = {m: [] for m in METHODS}
    for q in qs:
        cells = []
        for m in METHODS:
            reps = []
            for _ in range(5):
                t0 = time.perf_counter()
                files = loaded[m].files(q["ask"])
                reps.append((time.perf_counter() - t0) * 1000)
            warm[m].append(statistics.median(reps))
            r = rank_of(files[:50], q["expect"])
            right[m][q["set"]] += r is not None and r <= 3
            right_d[m][q["set"]] += any(
                any(e in f for e in q["expect"]) for f in distinct(files)[:3]
            )
            cells.append("-" if r is None else str(r))
        print(f"{q['id']!s:<5}" + "".join(f"{c:>11}" for c in cells) + f"  {q['ask']}")
    sets = sorted({q["set"] for q in qs})
    totals = Counter(q["set"] for q in qs)
    print("\nright first (first three NODE lines) / (first three distinct files)")
    for m in METHODS:
        print(
            f"{m:<10} "
            + "  ".join(f"{s} {right[m][s]}/{totals[s]} ({right_d[m][s]})" for s in sets)
        )
    print("\nlatency, ms: load in-process; warm = median over questions of the median of 5 runs")
    cold_q = qs[0]["ask"]
    for m in METHODS:
        walls = []
        for _ in range(5):
            t0 = time.perf_counter()
            subprocess.run(
                [sys.executable, "-I", __file__, str(data), str(models), "--one", m, cold_q],
                check=True,
                capture_output=True,
            )
            walls.append((time.perf_counter() - t0) * 1000)
        print(
            f"{m:<10} load {load_ms[m]:7.1f}  warm {statistics.median(warm[m]):7.2f} "
            f"(max {max(warm[m]):.2f})  cold process {statistics.median(walls):7.1f} "
            f"(min {min(walls):.1f}, max {max(walls):.1f})"
        )
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-I", "-c", "pass"], check=True)
    print(f"bare `python3 -I -c pass`: {(time.perf_counter() - t0) * 1000:.1f} ms")
    print("\nbundle bytes added (raw / zlib -6, what a git object stores before deltas)")
    texts = data / "bm25-texts.json"
    texts.write_text(
        json.dumps([n["text"] for n in json.loads((data / "nodes.json").read_text())]),
        encoding="utf-8",
    )
    print(f"bm25       node texts {texts.stat().st_size} / {gz(texts)}")
    for size in ("2M", "8M"):
        parts = [
            models / f"potion-base-{size}" / "model.safetensors",
            models / f"potion-base-{size}" / "tokenizer.json",
            data / f"vec-{size}.f32",
        ]
        raw = sum(p.stat().st_size for p in parts)
        print(
            f"potion-{size}  "
            + ", ".join(f"{p.name} {p.stat().st_size}" for p in parts)
            + f"; total {raw} / {sum(gz(p) for p in parts)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
