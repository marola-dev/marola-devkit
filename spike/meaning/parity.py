#!/usr/bin/env python3
"""MIP-0085 §7 `query_vector_parity` for the spike: the stdlib query vector against model2vec's.

    parity.py MODEL_DIR QUESTIONS_JSON NODES_JSON

Strings: every question, edge cases (accents, Portuguese, CJK, emoji, control characters, a word
over 100 characters, code), and node texts up to 50 in all; then token ids on every node text.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from static_embed import StaticModel as StdlibModel  # noqa: E402

EDGE = [
    "Onde a água é IMPRÓPRIA para banho?",
    "praia do Campeche, Florianópolis — maré às 14h",
    "naïve café résumé coöperate",
    "海の波 wind 风",
    "swim 🏊 now 🌊!",
    "tab\there\nnewline\r\x00nul\x07bell​zwsp�repl",
    "x" * 101 + " short",
    "OpenMeteoClient.fetchHourly(lat, lon)",
    "def score(h: HourlyForecast) -> Int = 0 // veto",
    "https://overpass-api.de/api/interpreter?data=[out:json];",
    "İstanbul ǅemal ß ﬁ ℌ",
    "",
    "   ",
]


def main(argv: list[str]) -> int:
    from model2vec import StaticModel

    model_dir = Path(argv[0])
    qs = [q["ask"] for q in json.loads(Path(argv[1]).read_text())["questions"]]
    nodes = [n["text"] for n in json.loads(Path(argv[2]).read_text())]
    random.seed(85)
    strings = (qs + EDGE + random.sample(nodes, 50))[:50]
    ref = StaticModel.from_pretrained(str(model_dir))
    mine = StdlibModel(model_dir)
    worst, id_miss = 0.0, 0
    for s in strings:
        a = ref.encode([s])[0].tolist()
        b = mine.encode(s)
        na, nb = sum(x * x for x in a) ** 0.5, sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            gap = 0.0 if na == nb == 0 else 1.0
        else:
            gap = 1 - sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)
        worst = max(worst, abs(gap))
        id_miss += (
            ref.tokenize([s], max_length=512)[0]
            != [i for i in mine.tok.ids(s[: 512 * mine.median]) if i != mine.tok.unk_id][:512]
        )
    node_miss = sum(
        ref.tokenizer.encode(t, add_special_tokens=False).ids != mine.tok.ids(t) for t in nodes
    )
    print(
        f"{model_dir.name}: {len(strings)} strings, worst 1-cos {worst:.2e}, "
        f"token-id mismatches {id_miss}; node texts {len(nodes)}, token-id mismatches {node_miss}"
    )
    return 0 if worst <= 1e-5 and id_miss == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
