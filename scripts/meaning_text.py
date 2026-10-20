#!/usr/bin/env python3
"""One text per code-graph node for `route query --meaning` (MIP-0085 §5.7): the node's label, the
docstring and leading comment at its line in the source, and its file path.

meaning_text --graph GRAPH_JSON --out FILE [--root DIR]   write the bundle's meaning.json
meaning_text --self-test

DIR is the checkout the graph was built from, submodules initialised (the current directory by
default). The file is {"schema": 1, "nodes": {"<node id>": "<text>"}}, ids sorted, one per line.
The text is left raw: route splits it with the tokens() it splits the question with, so the two
never drift.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

SCHEMA = 1
CAP = 1500
COMMENT = re.compile(r"^\s*(//+|#+|--+|\*+/?|/\*+)\s?")
PYSTR = re.compile(r"^\s*[rRbBuUfF]{0,2}(\"\"\"|'''|\"|')")
PREAMBLE = ("package ", "import ", "set -", "'use strict'", '"use strict"')


def strip_marks(lines: list[str]) -> str:
    out = []
    for ln in lines:
        s = re.sub(r"\*+/$", "", COMMENT.sub("", ln).strip()).strip()
        if s and not s.startswith("!"):
            out.append(s)
    return " ".join(out)


def comment_above(src: list[str], i: int) -> str:
    """The comment lines right above line index i, past its annotations and decorators."""
    j = i - 1
    while j >= 0 and src[j].strip().startswith("@"):
        j -= 1
    got: list[str] = []
    if j >= 0 and src[j].rstrip().endswith("*/"):
        while j >= 0:
            got.append(src[j])
            if "/*" in src[j]:
                break
            j -= 1
    else:
        while j >= 0:
            s = src[j].strip()
            if not (s.startswith(("//", "--")) or (s.startswith("#") and not s.startswith("#!"))):
                break
            got.append(src[j])
            j -= 1
    return strip_marks(got[::-1])


def py_string_at(src: list[str], k: int) -> str:
    while k < len(src) and not src[k].strip():
        k += 1
    m = PYSTR.match(src[k]) if k < len(src) else None
    if not m:
        return ""
    q, body = m.group(1), src[k][m.end() :]
    if q in body:
        return body.split(q, 1)[0].strip()
    parts = [body]
    for ln in src[k + 1 : k + 200]:
        if q in ln:
            parts.append(ln.split(q, 1)[0])
            break
        parts.append(ln)
    return " ".join(p.strip() for p in parts if p.strip())


def py_docstring(src: list[str], i: int) -> str:
    """The docstring under the def or class at line index i, past a signature over several lines."""
    j = i
    while j < len(src) and src[j].strip().startswith("@"):
        j += 1
    depth = 0
    for k in range(j, min(j + 30, len(src))):
        code = src[k].split("#", 1)[0]
        depth += code.count("(") + code.count("[") - code.count(")") - code.count("]")
        if depth <= 0 and code.rstrip().endswith(":"):
            return py_string_at(src, k + 1)
    return ""


def file_head(src: list[str], ext: str) -> str:
    """A file's leading comment, or a Python module's docstring, before any code."""
    k = 1 if src and src[0].startswith("#!") else 0
    if ext == "py":
        while k < len(src) and (not src[k].strip() or src[k].lstrip().startswith("#")):
            k += 1
        return " ".join(x for x in (comment_above(src, k), py_string_at(src, k)) if x)
    got: list[str] = []
    block = False
    for ln in src[k:]:
        s = ln.strip()
        if block:
            got.append(ln)
            block = "*/" not in s
        elif not s or s.startswith(PREAMBLE):
            if got and not s:
                break
        elif s.startswith("/*"):
            got.append(ln)
            block = "*/" not in s
        elif s.startswith(("//", "#", "--")):
            got.append(ln)
        else:
            break
    return strip_marks(got)


def lines_of(root: Path, path: str, cache: dict[str, list[str]]) -> list[str]:
    if path not in cache:
        cache[path] = []
        p = (root / path).resolve()
        # source_file comes from the graph, which is data: never read outside the checkout.
        if path and p.is_relative_to(root.resolve()) and p.is_file():
            cache[path] = p.read_text(encoding="utf-8", errors="replace").split("\n")
    return cache[path]


def node_text(node: dict, root: Path, cache: dict[str, list[str]]) -> tuple[str, bool]:
    """(label, doc, path joined; whether a doc was found). A rationale node's label is its text."""
    path = str(node.get("source_file") or "")
    label = str(node.get("label") or "")
    doc = ""
    if node.get("file_type") != "rationale":
        src = lines_of(root, path, cache)
        m = re.match(r"L?(\d+)", str(node.get("source_location") or ""))
        line = int(m.group(1)) if m else 1
        ext = path.rsplit(".", 1)[-1]
        kind = (node.get("metadata") or {}).get("kind")
        i = line - 1
        if line == 1 or kind == "file" or label.startswith((path, Path(path).name)):
            # A Scala file's first comment can belong to its first class (MIP-0085 §Appendix).
            doc = file_head(src, ext)
        elif 0 <= i < len(src):
            doc = comment_above(src, i)
            if ext == "py":
                doc = " ".join(x for x in (doc, py_docstring(src, i)) if x)
            if ext == "sh" and " # " in src[i]:
                doc = " ".join(x for x in (doc, src[i].split(" # ", 1)[1].strip()) if x)
    doc = doc[:CAP]
    return " ".join(x for x in (label, doc, path) if x), bool(doc)


def build(graph: dict, root: Path) -> tuple[dict, int]:
    cache: dict[str, list[str]] = {}
    nodes, with_doc = {}, 0
    for n in graph.get("nodes", []):
        if isinstance(n, dict) and "id" in n:
            text, doc = node_text(n, root, cache)
            nodes[str(n["id"])] = text
            with_doc += doc
    return {"schema": SCHEMA, "nodes": dict(sorted(nodes.items()))}, with_doc


def dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=0) + "\n"


# ---------------------------------------------------------------- self-test

FIXTURES = {
    "app/Veto.scala": """package marola.scoring

/** The safety veto: a dangerous flag
  * forbids the swim. */
@deprecated("x")
final case class SafetyVeto(flag: Flag)

// Unrelated, two lines below a blank.

object Plain
""",
    "ml/train.py": '''#!/usr/bin/python3
"""Assemble the fine-tuning data."""

import os

# Reads every corpus file.
@cache
def load(
    path: str,
) -> list:
    """Return the rows,
    deduplicated."""
    return []
''',
    "scripts/x.sh": """#!/bin/bash
# Fetch the corpus release asset.
set -euo pipefail

fetch() {  # download and verify the tarball
  :
}
""",
}


def self_test() -> int:
    fails = 0

    def ok(name: str, got, want) -> None:
        nonlocal fails
        if got == want:
            print(f"  ok   {name}")
        else:
            fails += 1
            print(f"  FAIL {name} — got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "umb"
        for rel, text in FIXTURES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        (Path(tmp) / "secret.sh").write_text("# SECRET\n", encoding="utf-8")

        def text(label: str, path: str, loc: str = "L1", **extra) -> str:
            node = {"id": label, "label": label, "source_file": path, "source_location": loc}
            return node_text({**node, **extra}, root, {})[0]

        ok(
            "scala_doc_comment_above",
            (text("SafetyVeto", "app/Veto.scala", "L6"), text("Plain", "app/Veto.scala", "L10")),
            (
                "SafetyVeto The safety veto: a dangerous flag forbids the swim. app/Veto.scala",
                "Plain app/Veto.scala",
            ),
        )
        ok(
            "python_comment_and_docstring",
            text("load()", "ml/train.py", "L8"),
            "load() Reads every corpus file. Return the rows, deduplicated. ml/train.py",
        )
        ok(
            "file_node_head",
            (
                text("train.py", "ml/train.py"),
                text("x.sh", "scripts/x.sh", kind="file"),
            ),
            (
                "train.py Assemble the fine-tuning data. ml/train.py",
                "x.sh Fetch the corpus release asset. scripts/x.sh",
            ),
        )
        ok(
            "shell_trailing_comment",
            text("fetch()", "scripts/x.sh", "L5"),
            "fetch() download and verify the tarball scripts/x.sh",
        )
        ok(
            "rationale_and_missing_file_label_and_path",
            (
                text("Why the veto", "app/Veto.scala", "L6", file_type="rationale"),
                text("Gone", "app/Gone.scala", "L3"),
            ),
            ("Why the veto app/Veto.scala", "Gone app/Gone.scala"),
        )
        ok(
            "path_outside_root_not_read",
            text("leak", "../secret.sh"),
            "leak ../secret.sh",
        )
        graph = {
            "nodes": [
                {
                    "id": "b",
                    "label": "SafetyVeto",
                    "source_file": "app/Veto.scala",
                    "source_location": "L6",
                },
                {
                    "id": "a",
                    "label": "load()",
                    "source_file": "ml/train.py",
                    "source_location": "L8",
                },
            ]
        }
        first, with_doc = build(graph, root)
        again, _ = build({"nodes": graph["nodes"][::-1]}, root)
        ok(
            "output_deterministic",
            (list(first["nodes"]), first["schema"], with_doc, dump(first) == dump(again)),
            (["a", "b"], 1, 2, True),
        )
    if fails:
        print(f"meaning_text self-test: {fails} failure(s)", file=sys.stderr)
        return 1
    print("meaning_text self-test: ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--graph", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.graph or not args.out:
        ap.error("--graph and --out are required")
    t0 = time.perf_counter()
    data, with_doc = build(json.loads(args.graph.read_text(encoding="utf-8")), args.root)
    text = dump(data)
    args.out.write_text(text, encoding="utf-8")
    ms = (time.perf_counter() - t0) * 1000
    print(
        f"meaning_text: {len(data['nodes'])} nodes, {with_doc} with a docstring or comment, "
        f"{len(text.encode())} B, {ms:.0f} ms → {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
