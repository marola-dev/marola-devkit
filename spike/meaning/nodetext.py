#!/usr/bin/env python3
"""MIP-0085 §5.7 spike (throwaway): one text per graph node, the same for every candidate.

    nodetext.py GRAPH_JSON SOURCE_TREE OUT_JSON

The text is the label's identifiers split, the docstring and leading comment at the node's line,
and the file path split. SOURCE_TREE is the umbrella checkout (submodules initialised) at the commit
the graph was built from.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/routing/scripts"))
from route import tokens  # noqa: E402

CAP = 1500
COMMENT = re.compile(r"^\s*(//+|#+|--+|\*+/?|/\*+)\s?")
PYSTR = re.compile(r"^\s*[rRbBuUfF]{0,2}(\"\"\"|'''|\"|')")


def strip_marks(lines: list[str]) -> str:
    out = []
    for ln in lines:
        s = COMMENT.sub("", ln).strip()
        s = re.sub(r"\*+/$", "", s).strip()
        if s and not s.startswith("!"):
            out.append(s)
    return " ".join(out)


def comment_above(src: list[str], i: int) -> str:
    """Contiguous comment lines right above line index i, skipping annotations and decorators."""
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
        return strip_marks(list(reversed(got)))
    while j >= 0:
        s = src[j].strip()
        if s.startswith(("//", "--")) or (s.startswith("#") and not s.startswith("#!")):
            got.append(src[j])
            j -= 1
        else:
            break
    return strip_marks(list(reversed(got)))


def py_string_at(src: list[str], k: int) -> str:
    """The string literal starting at line index k, if any."""
    while k < len(src) and not src[k].strip():
        k += 1
    if k >= len(src):
        return ""
    m = PYSTR.match(src[k])
    if not m:
        return ""
    q = m.group(1)
    body = src[k][m.end() :]
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
    """A file's leading comment (or Python module docstring), before any code."""
    k = 0
    if src and src[0].startswith("#!"):
        k = 1
    if ext == "py":
        while k < len(src) and (not src[k].strip() or src[k].lstrip().startswith("#")):
            k += 1
        doc = py_string_at(src, k)
        head = comment_above(src, k)
        return " ".join(x for x in (head, doc) if x)
    got: list[str] = []
    in_block = False
    for ln in src[k:]:
        s = ln.strip()
        if in_block:
            got.append(ln)
            in_block = "*/" not in s
            continue
        if not s or s.startswith(("package ", "import ", "set -", "'use strict'", '"use strict"')):
            if got and not s:
                break
            continue
        if s.startswith("/*"):
            got.append(ln)
            in_block = "*/" not in s
            continue
        if s.startswith(("//", "#", "--")):
            got.append(ln)
            continue
        break
    return strip_marks(got)


def node_text(node: dict, root: Path, cache: dict) -> str:
    path = str(node.get("source_file") or "")
    label = str(node.get("label") or "")
    parts = [" ".join(tokens(label))]
    if node.get("file_type") != "rationale":
        if path not in cache:
            try:
                cache[path] = (
                    (root / path).read_text(encoding="utf-8", errors="replace").split("\n")
                )
            except OSError:
                cache[path] = []
        src = cache[path]
        line = int(str(node.get("source_location") or "L1").lstrip("L").split("-")[0] or 1)
        ext = path.rsplit(".", 1)[-1]
        kind = (node.get("metadata") or {}).get("kind")
        is_file = line == 1 or label.startswith((path, Path(path).name)) or kind == "file"
        if is_file:
            doc = file_head(src, ext)
        else:
            i = line - 1
            doc = comment_above(src, i) if 0 <= i < len(src) else ""
            if ext == "py" and 0 <= i < len(src):
                doc = " ".join(x for x in (doc, py_docstring(src, i)) if x)
            if ext == "sh" and 0 <= i < len(src) and " # " in src[i]:
                doc = " ".join(x for x in (doc, src[i].split(" # ", 1)[1].strip()) if x)
        parts.append(doc[:CAP])
    parts.append(" ".join(tokens(path)))
    return " ".join(p for p in parts if p)


def main(argv: list[str]) -> int:
    graph, root, out = json.loads(Path(argv[0]).read_text()), Path(argv[1]), Path(argv[2])
    t0 = time.perf_counter()
    cache: dict = {}
    rows = [
        {
            "id": n["id"],
            "label": n.get("label", ""),
            "file": n.get("source_file", ""),
            "loc": n.get("source_location", ""),
            "text": node_text(n, root, cache),
        }
        for n in graph["nodes"]
    ]
    dt = time.perf_counter() - t0
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    empty = sum(
        1
        for n, r in zip(graph["nodes"], rows, strict=True)
        if r["text"] == " ".join(tokens(n.get("label", "")) + tokens(n.get("source_file", "")))
    )
    print(f"nodes {len(rows)}; without a doc or comment {empty}; {dt * 1000:.0f} ms; {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
