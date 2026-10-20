#!/usr/bin/env python3
"""The decisions in the MIPs, one row per item (MIP-0085 §5.6): §1's first sentence, each
"Alternatives considered" bullet's bold lead and first clause, and every
`**Decided YYYY-MM-DD:** …` line, with the MIP's section and status.

decisions [--mips DIR] [--md FILE] [--json FILE]   write the table (FILE defaults to DIR/DECISIONS.md)
                                                   and/or the rows as JSON (route why reads it)
decisions --check [FILE] [--mips DIR]              fail when FILE differs from what the MIPs give
decisions --self-test

DIR defaults to docs/MIPs under the repo root.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SECNUM = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
STATUS = re.compile(r"^\|\s*\*\*Status\*\*\s*\|\s*(.*?)\s*\|\s*$")
MARKER = re.compile(r"\*\*Decided \d{4}-\d{2}-\d{2}:")
ALTERNATIVES = re.compile(r"\bAlternatives\b")
MIP_FILE = re.compile(r"(MIP-\d{4})-.+\.md")
ABBREV = re.compile(r"(?:\be\.g|\bi\.e|\bvs|\bcf|\betc|\bapprox)$", re.I)


def blocks(text: str):
    """(section, in_alternatives, indent or None for a paragraph, joined text) per bullet or
    paragraph outside fences and tables; a wrapped line joins the block it continues."""
    section, alt, fence, cur = "", 0, None, None
    for line in text.splitlines() + [""]:
        if fence:
            if re.match(rf"^\s*{fence[0]}{{{len(fence)},}}\s*$", line):
                fence = None
            continue
        f, h, b = FENCE.match(line), HEADING.match(line), BULLET.match(line)
        if cur and (f or h or b or not line.strip() or line.lstrip().startswith("|")):
            yield cur[0], cur[1], cur[2], " ".join(cur[3])
            cur = None
        if f:
            fence = f.group(1)
        elif h:
            level, num = len(h.group(1)), SECNUM.match(h.group(2) + " ")
            section = num.group(1) if num else h.group(2)
            if alt and level <= alt:
                alt = 0
            if not alt and level >= 2 and ALTERNATIVES.search(h.group(2)):
                alt = level
        elif b:
            cur = (section, bool(alt), len(b.group(1)), [b.group(2).strip()])
        elif cur:
            cur[3].append(line.strip())
        elif line.strip() and not line.lstrip().startswith("|"):
            cur = (section, bool(alt), None, [line.strip()])


def stop(s: str, clause: bool, commas: bool = True) -> int:
    """Where s's first sentence (or clause) ends, outside code spans, brackets and parentheses;
    a sentence end keeps its full stop."""
    depth, code = 0, False
    for i, c in enumerate(s):
        nxt = s[i + 1 : i + 2]
        if c == "`":
            code = not code
        elif code:
            continue
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        elif depth:
            continue
        elif c in ".?!" and nxt in ("", " ") and not ABBREV.search(s[:i]):
            return i + 1
        # A comma ends a clause only after three words: "Free, but it leaves…" is not "Free".
        elif (
            clause
            and nxt == " "
            and (c in ";:" or (commas and c == "," and len(s[:i].split()) >= 3))
        ):
            return i
        elif clause and c in "—–" and s[i - 1 : i] == " " and nxt == " ":
            return i - 1
    return len(s)


def first(s: str, clause: bool = False) -> str:
    return s[: stop(s, clause)].strip()


def alternatives(bullet: str) -> list[str]:
    # A bullet may hold several bold-led alternatives, one per sentence (MIP-0084 §9).
    items, rest = [], bullet
    while rest:
        end = stop(rest, False)
        while end < len(rest) and not rest[end:].lstrip().startswith("**"):
            end += stop(rest[end:], False)
        items.append(rest[:end].strip())
        rest = rest[end:].strip()
    out = []
    for item in items:
        plain = item.replace("**", "")
        if item.startswith("**"):
            end = stop(plain, True, commas=False)
            lead = plain[:end].strip().rstrip(".:;")
            clause = first(plain[end:].lstrip(" .:;—–-"), clause=True).rstrip(".")
        else:
            lead, clause = first(plain, clause=True).rstrip("."), ""
        if lead:
            out.append(f"{lead} (rejected): {clause}" if clause else f"{lead} (rejected)")
    return out


def status_of(text: str) -> str:
    fence = None
    for line in text.splitlines():
        if f := FENCE.match(line):
            fence = None if fence else f.group(1)
        elif not fence and (m := STATUS.match(line)):
            word = re.match(r"[A-Za-z][A-Za-z ]*", m.group(1))
            return word.group(0).strip() if word else "?"
    return "?"


def parse_mip(name: str, text: str) -> list[dict]:
    m = MIP_FILE.fullmatch(name)
    if not m:
        return []
    base = {"mip": m.group(1), "file": name, "status": status_of(text)}
    rows, summary = [], False
    for section, alt, indent, block in blocks(text):
        if not summary and (section == "1" or section.lower() == "summary"):
            summary = True
            rows.append(
                {
                    **base,
                    "section": section,
                    "kind": "summary",
                    "text": first(block.replace("**", "")),
                }
            )
        if alt and indent == 0:
            for t in alternatives(block):
                rows.append({**base, "section": section, "kind": "alternative", "text": t})
        for mk in MARKER.finditer(block):
            if block[: mk.start()].count("`") % 2:
                continue
            t = first(block[mk.start() :].replace("**", ""))
            rows.append({**base, "section": section, "kind": "decided", "text": t})
    return rows


def rows_for(mips: Path) -> list[dict]:
    rows = []
    for f in sorted(mips.glob("MIP-[0-9][0-9][0-9][0-9]-*.md")):
        rows += parse_mip(f.name, f.read_text(encoding="utf-8"))
    return rows


HEADER = """# MIP decisions

Generated by marola-devkit's `decisions` from each MIP's §1 first sentence, its "Alternatives
considered" bullets and its `**Decided YYYY-MM-DD:**` lines (MIP-0085 §5.6). Edit the MIPs, not
this file; `route why "<words>"` ranks these rows.

| MIP | § | Status | Kind | Decision |
|---|---|---|---|---|
"""


def render_md(rows: list[dict]) -> str:
    def cell(s: str, file: str = "") -> str:
        # An in-page link would point into this file, and the umbrella's strict build fails on it.
        return s.replace("|", "\\|").replace("](#", f"]({file}#")

    lines = [
        f"| [{r['mip']}]({r['file']}) | {cell(r['section'])} | {r['status']} | {r['kind']} "
        f"| {cell(r['text'], r['file'])} |"
        for r in rows
    ]
    return HEADER + "\n".join(lines) + "\n"


def check(path: Path, mips: Path) -> int:
    want = render_md(rows_for(mips))
    if not path.is_file():
        print(f"decisions: {path} is missing; run `decisions` to write it", file=sys.stderr)
        return 1
    if path.read_text(encoding="utf-8") != want:
        print(f"decisions: {path} is stale; run `decisions` to regenerate it", file=sys.stderr)
        return 1
    print(f"decisions: {path} is current")
    return 0


MIP = """# MIP-0042: A test

| | |
|---|---|
| **Status** | Accepted (2026-10-10, review by someone) |

## 1. Summary

The routing bundle moves to `refs/x.y`, e.g. a branch ([§5](#5-design)). It is fetched once.

## 5. Design

### 5.2 Hooks

The hook is read-only. **Decided 2026-10-10:** either answer is fine. Someone may be asked later.
Write a decision as `**Decided YYYY-MM-DD:** <decision>`.

**Decided 2026-10-03: the wiring lives in
`REPOS.md`.** REPOS.md's table stays.

## 9. Alternatives considered

- **graphify's LLM pass**: about 0.6–0.7M input tokens per full pass, edges inferred; rejected.
- **Do nothing.** A GitHub URL breaks when a repo moves.
- **A release asset** (§5.3): mints Zenodo versions. **An Actions artifact**: expires, and needs a
  token.
  - a nested detail, not an alternative
- A plain bullet; no bold lead.

## 11. Open questions

- Which scope? **Default:** user.
"""


def _texts(rows: list[dict], kind: str) -> list[tuple[str, str]]:
    return [(r["section"], r["text"]) for r in rows if r["kind"] == kind]


def self_test() -> int:
    fails = 0

    def ok(name: str, got, want) -> None:
        nonlocal fails
        if got == want:
            print(f"  ok   {name}")
        else:
            fails += 1
            print(f"  FAIL {name} — got {got!r}, want {want!r}")

    def run(name: str, fn) -> None:
        nonlocal fails
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — a crash is one failed case, not the end of the run
            fails += 1
            print(f"  FAIL {name} — {type(e).__name__}: {e}")

    def summary_first_sentence() -> None:
        rows = parse_mip("MIP-0042-a-test.md", MIP)
        ok(
            "summary_first_sentence",
            (
                _texts(rows, "summary"),
                {r["status"] for r in rows},
                {r["mip"] for r in rows},
            ),
            (
                [("1", "The routing bundle moves to `refs/x.y`, e.g. a branch ([§5](#5-design)).")],
                {"Accepted"},
                {"MIP-0042"},
            ),
        )

    def alternative_bold_lead() -> None:
        ok(
            "alternative_bold_lead",
            _texts(parse_mip("MIP-0042-a-test.md", MIP), "alternative"),
            [
                ("9", "graphify's LLM pass (rejected): about 0.6–0.7M input tokens per full pass"),
                ("9", "Do nothing (rejected): A GitHub URL breaks when a repo moves"),
                ("9", "A release asset (§5.3) (rejected): mints Zenodo versions"),
                ("9", "An Actions artifact (rejected): expires, and needs a token"),
                ("9", "A plain bullet (rejected)"),
            ],
        )

    def decided_marker_row() -> None:
        ok(
            "decided_marker_row",
            _texts(parse_mip("MIP-0042-a-test.md", MIP), "decided"),
            [
                ("5.2", "Decided 2026-10-10: either answer is fine."),
                ("5.2", "Decided 2026-10-03: the wiring lives in `REPOS.md`."),
            ],
        )

    def check_fails_on_stale_file() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mips = Path(tmp)
            (mips / "MIP-0042-a-test.md").write_text(MIP, encoding="utf-8")
            (mips / "MIP-0042.tasks.md").write_text("# tasks\n", encoding="utf-8")
            out = mips / "DECISIONS.md"
            missing = check(out, mips)
            out.write_text(render_md(rows_for(mips)), encoding="utf-8")
            fresh = check(out, mips)
            ok(
                "table_links_in_page_anchors_to_the_mip",
                "](MIP-0042-a-test.md#5-design)" in out.read_text(),
                True,
            )
            (mips / "MIP-0042-a-test.md").write_text(
                MIP.replace("either answer", "neither answer"), encoding="utf-8"
            )
            stale = check(out, mips)
            ok("check_fails_on_stale_file", (missing, fresh, stale), (1, 0, 1))

    for name, fn in [
        ("summary_first_sentence", summary_first_sentence),
        ("alternative_bold_lead", alternative_bold_lead),
        ("decided_marker_row", decided_marker_row),
        ("check_fails_on_stale_file", check_fails_on_stale_file),
    ]:
        run(name, fn)
    if fails:
        print(f"decisions self-test: {fails} failure(s)", file=sys.stderr)
        return 1
    print("decisions self-test: ok")
    return 0


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    return Path(out.stdout.strip()) if out.returncode == 0 else Path.cwd()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--mips", type=Path)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--check", nargs="?", const="", metavar="FILE")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    mips = args.mips or _repo_root() / "docs" / "MIPs"
    if not mips.is_dir():
        print(f"decisions: {mips} is not a directory", file=sys.stderr)
        return 2
    if args.check is not None:
        return check(Path(args.check) if args.check else mips / "DECISIONS.md", mips)
    rows = rows_for(mips)
    md = args.md or (None if args.json else mips / "DECISIONS.md")
    if md:
        md.write_text(render_md(rows), encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1) + "\n")
    print(f"decisions: {len(rows)} rows from {len({r['mip'] for r in rows})} MIPs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
