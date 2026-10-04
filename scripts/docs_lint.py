#!/usr/bin/env python3
"""Stale-content check over README.md and docs/**/*.md (MIP-0074 §7, rules a–e).

docs-lint [repo-root]
docs-lint --self-test
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Owner of each top-level directory is the one repo that has it at its root.
OWNED_DIRS = ("core", "local", "cli", "finetune", "dspy", "knowledge", "site")
OWNED_PATH = re.compile(r"(?<![\w.:/~-])(?:\.\.?/)*(" + "|".join(OWNED_DIRS) + r")/")
NUMBERED_DIRS = re.compile(r"docs/(?:1-Using|2-Building)-marola")
STALE = [
    (re.compile(r"marola-dev/marola/blob/main/docs/2-"), "the umbrella's old docs/2- path"),
    (re.compile(r"github\.com/h0ffmann/marola\b"), "the pre-org repo URL"),
    (re.compile(r"\bmonorepo", re.I), 'the word "monorepo"'),
]
URL = re.compile(r"\[[^\]]*\]\(\s*<?[a-zA-Z][\w+.-]*://[^)]*\)|[a-zA-Z][\w+.-]*://[^\s)>\]]+")
CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
JUST = re.compile(r"(?:^|[\s;&|(])just\s+([A-Za-z_][\w-]*)")
MARKER = re.compile(r"\bin a marola-[a-z]+ checkout\b", re.I)
FENCE_MARKER = re.compile(r"#\s*in a marola-[a-z]+ checkout", re.I)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|\|)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
LINK_TARGETS = [
    re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)"),
    re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)"),
    re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)["']"""),
]
RECIPE = re.compile(r"^@?([A-Za-z_][\w-]*)[^:\n]*:(?!=)", re.M)
ALIAS = re.compile(r"^alias\s+([\w-]+)\s*:=", re.M)


def recipes(root: Path) -> set[str]:
    devkit = root / ".devkit" / "devkit.just"
    if not devkit.is_file():
        devkit = Path(__file__).resolve().parent.parent / "devkit.just"
    names: set[str] = set()
    for f in (root / "justfile", root / "Justfile", root / ".justfile", devkit):
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            names |= set(RECIPE.findall(text)) | set(ALIAS.findall(text))
    return names


def is_umbrella(root: Path) -> bool:
    gm = root / ".gitmodules"
    return gm.is_file() and "marola-dev/marola-app" in gm.read_text(encoding="utf-8")


def split(lines: list[str]):
    """Yield ("fence"|"prose", first line number, lines) blocks."""
    i, block, start = 0, [], 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if m or not lines[i].strip() or BLOCK_START.match(lines[i]):
            if block:
                yield "prose", start, block
            block = []
        if m:
            fence, j = m.group(1), i + 1
            while j < len(lines) and not re.match(
                rf"^\s*{fence[0]}{{{len(fence)},}}\s*$", lines[j]
            ):
                j += 1
            yield "fence", i + 2, lines[i + 1 : j]
            i = j + 1
            continue
        if lines[i].strip():
            if not block:
                start = i + 1
            block.append(lines[i])
        i += 1
    if block:
        yield "prose", start, block


def lint_file(text: str, rel: Path, known: set[str], owned: set[str], umbrella: bool):
    out = []
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        for pat, what in STALE:
            if pat.search(line):
                out.append((n, "c", what))
        if not umbrella and (m := NUMBERED_DIRS.search(line)):
            out.append((n, "c", f"{m.group(0)} is the umbrella's"))
        for m in OWNED_PATH.finditer(URL.sub(" ", line)):
            if m.group(1) not in owned:
                out.append((n, "b", f"{m.group(1)}/ belongs to another repo; link it by URL"))

    for kind, start, block in split(lines):
        if kind == "fence":
            if block and FENCE_MARKER.fullmatch(block[0].strip()):
                continue
            for k, line in enumerate(block):
                code = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
                for m in JUST.finditer(code):
                    if m.group(1) not in known:
                        out.append((start + k, "a", f"`just {m.group(1)}` is not a recipe here"))
            continue
        para = "\n".join(block)
        bounds = [0, *(m.end() for m in SENTENCE_END.finditer(para)), len(para)]
        for s, e in zip(bounds, bounds[1:], strict=False):
            sentence = para[s:e]
            if MARKER.search(sentence):
                continue
            for span in CODE_SPAN.finditer(sentence):
                for m in JUST.finditer(span.group(2)):
                    if m.group(1) not in known:
                        n = start + para[: s + span.start()].count("\n")
                        out.append((n, "a", f"`just {m.group(1)}` is not a recipe here"))
        for k, line in enumerate(block):
            for pat in LINK_TARGETS:
                for m in pat.finditer(CODE_SPAN.sub("", line)):
                    target = re.split(r"[#?]", m.group(1), maxsplit=1)[0]
                    if (
                        not target
                        or target.startswith("/")
                        or re.match(r"[a-zA-Z][\w+.-]*:", target)
                    ):
                        continue
                    if os.path.normpath(rel.parent / target).startswith(".."):
                        out.append((start + k, "e", f"{m.group(1)} leaves the repo"))
    return out


KEPT_RECORDS = {("docs", "MIPs"), ("docs", "benchmarks")}


def lint(root: Path) -> list[tuple[str, int, str, str]]:
    findings = []
    if (root / "docs" / "index.md").is_file():
        findings.append(("docs/index.md", 1, "d", "the README is the landing; drop docs/index.md"))
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    known, umbrella = recipes(root), is_umbrella(root)
    owned = {d for d in OWNED_DIRS if (root / d).is_dir()}
    for f in files:
        rel = f.relative_to(root)
        # Dated records stay as written: MIPs and SPLIT.md, and benchmark runs kept off-site.
        if not f.is_file() or f.name == "SPLIT.md" or rel.parts[:2] in KEPT_RECORDS:
            continue
        text = f.read_text(encoding="utf-8")
        for n, rule, msg in lint_file(text, rel, known, owned, umbrella):
            findings.append((rel.as_posix(), n, rule, msg))
    return findings


CASES = [
    (
        "recipe_defined_ok",
        {
            "justfile": "set shell := ['bash', '-c']\n\n# gates\nquality *args:\n    echo\n",
            "README.md": "Run `just quality` before a push, or `just pr` (the devkit's).\n\n```bash\njust quality\n```\n",
        },
        set(),
    ),
    ("foreign_recipe_fails", {"README.md": "Run `just e2e` first.\n"}, {"a"}),
    (
        "foreign_recipe_marked_in_prose_ok",
        {"README.md": "Run `just e2e`\nin a marola-app checkout.\n"},
        set(),
    ),
    (
        "foreign_recipe_marked_in_fence_ok",
        {"README.md": "```bash\n# in a marola-app checkout\njust e2e\n```\n"},
        set(),
    ),
    (
        "marker_in_other_sentence_fails",
        {"README.md": "Clone it in a marola-app checkout. Then run `just e2e`.\n"},
        {"a"},
    ),
    ("app_path_in_prose_fails", {"docs/x.md": "The parser is in core/src/Parse.scala.\n"}, {"b"}),
    (
        "app_path_in_github_url_ok",
        {
            "docs/x.md": "See https://github.com/marola-dev/marola-app/blob/main/core/src/Parse.scala and\n"
            "[core/src/Parse.scala](https://github.com/marola-dev/marola-app/blob/main/core/src/Parse.scala).\n"
        },
        set(),
    ),
    ("app_path_in_fence_fails", {"docs/x.md": "```bash\ncat core/src/Parse.scala\n```\n"}, {"b"}),
    ("monorepo_word_fails", {"README.md": "Back in the Monorepo days.\n"}, {"c"}),
    (
        "mips_dir_allowlisted",
        {
            "docs/MIPs/MIP-0001-x.md": "The monorepo ran `just e2e` on core/x.scala.\n",
            "docs/2-Building/SPLIT.md": "The monorepo's core/ moved.\n",
        },
        set(),
    ),
    (
        "benchmark_record_allowlisted",
        {"docs/benchmarks/2026-09-05.md": "Ran `just e2e` over core/ and knowledge/.\n"},
        set(),
    ),
    ("docs_index_fails", {"docs/index.md": "# Home\n"}, {"d"}),
    (
        "escaping_link_fails",
        {"docs/x.md": "[ok](../README.md) and [up](../../marola-app/README.md)\n", "README.md": ""},
        {"e"},
    ),
]
UMBRELLA_TEXT = "See docs/1-Using-marola/RUN-LOCALLY.md and docs/2-Building-marola/REPOS.md.\n"
GITMODULES = '[submodule "marola-app"]\n\tpath = marola-app\n\turl = https://github.com/marola-dev/marola-app.git\n'


def _rules(files: dict[str, str]) -> set[str]:
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in files.items():
            (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / rel).write_text(text, encoding="utf-8")
        return {rule for _, _, rule, _ in lint(Path(tmp))}


def self_test() -> int:
    fails = 0

    def ok(name: str, got, want) -> None:
        nonlocal fails
        if got == want:
            print(f"  ok   {name}")
        else:
            fails += 1
            print(f"  FAIL {name} — rules {got}, want {want}")

    for name, files, want in CASES:
        ok(name, _rules(files), want)
    # The contrast is what proves the exemption: outside the umbrella the same text fails (c).
    umbrella = _rules({".gitmodules": GITMODULES, "README.md": UMBRELLA_TEXT})
    elsewhere = _rules({"README.md": UMBRELLA_TEXT})
    ok("umbrella_owns_its_numbered_dirs_ok", (umbrella, elsewhere), (set(), {"c"}))

    if fails:
        print(f"docs-lint self-test: {fails} failure(s)", file=sys.stderr)
        return 1
    print("docs-lint self-test: ok")
    return 0


def check(root: Path) -> int:
    findings = lint(root)
    for rel, line, rule, msg in findings:
        print(f"docs-lint: {rel}:{line}: ({rule}) {msg}", file=sys.stderr)
    if findings:
        return 1
    print("docs-lint: clean")
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
    ap.add_argument("root", nargs="?", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    return self_test() if args.self_test else check(args.root or _repo_root())


if __name__ == "__main__":
    sys.exit(main())
