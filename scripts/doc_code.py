#!/usr/bin/env python3
"""Which docs name which code: the backticked tokens in every repo's docs, matched against the code
graph's labels and the tracked files (MIP-0085 §5.4).

doc-code --root DIR --graph GRAPH [--md FILE] [--json FILE]
    DIR is an umbrella checkout with its submodules, GRAPH a graph.json from `graph build` or the
    routing bundle. Writes DOC-CODE.md (file level, by MIP and page) and doc_code.json (symbol
    level, both directions); with neither, prints the Markdown. Refuses to overwrite FILE with fewer
    than half its rows (MIP-0085 §8): delete it first to accept the drop
doc-code --root DIR --graph GRAPH --check FILE
    fail when FILE is not what the generator writes
doc-code --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TICK = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
MIP = re.compile(r"MIP-\d{4}")
NOT_A_NAME = re.compile(r"[*?{}<>$=\[\]]|://")
BARE = re.compile(r"[a-z]+")
MIN_LABEL = 4
# A dotted token with one of these endings names a file; any other is read as `Type.member`.
EXTS = frozenset(
    "py sh bash scala sbt js mjs ts json yml yaml toml nix cfg conf ini txt csv sql html css lock "
    "xml".split()
)
# Path segments too common in prose to point at one candidate.
GENERIC = frozenset(
    "main test tests src scala java python resources marola docs site core local index".split()
)
# Generated from other docs: scanning them would echo their sources (or this file's own output).
GENERATED = frozenset({"DOC-CODE.md", "DECISIONS.md"})


def git_files(d: Path) -> list[str] | None:
    if not (d / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(d), "ls-files", "-z"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [f for f in r.stdout.split("\0") if f] if r.returncode == 0 else None


def walk(d: Path, skip: set[str]) -> list[str]:
    out = []
    for top, dirs, names in os.walk(d):
        rel = Path(top).relative_to(d).as_posix()
        pre = "" if rel == "." else rel + "/"
        dirs[:] = [x for x in dirs if not x.startswith(".") and pre + x not in skip]
        out += [pre + n for n in names]
    return out


def tree(root: Path) -> list[tuple[str, str]]:
    """(umbrella-relative path, repo) for every tracked file of the umbrella and its submodules."""
    gm = root / ".gitmodules"
    subs = re.findall(r"^\s*path\s*=\s*(\S+)", gm.read_text() if gm.is_file() else "", re.M)
    out = [(f, "marola") for f in (git_files(root) or walk(root, set(subs))) if f not in subs]
    for p in subs:
        d = root / p
        if not d.is_dir() or not any(d.iterdir()):
            raise SystemExit(f"doc-code: {p} is not checked out (git submodule update --init)")
        out += [(f"{p}/{f}", Path(p).name) for f in (git_files(d) or walk(d, set()))]
    return sorted(out)


def last(name: str) -> str:
    return re.split(r"::|[.#]", re.sub(r"\(.*$", "", name.lstrip(".")))[-1]


def names(tok: str) -> list[str]:
    words = tok.split()
    # A command line names files only through its path arguments.
    picked = [w for w in words if "/" in w] if len(words) > 1 else words
    out = []
    for w in picked:
        w = re.sub(r":\d+(?:-\d+)?$", "", w.strip("'\",;")).removeprefix("./")
        if w and not w.endswith("/") and not NOT_A_NAME.search(w):
            out.append(w)
    return out


def segs(path: str, repo: str) -> set[str]:
    out = {repo}
    for part in path.split("/"):
        out |= {part, part.rsplit(".", 1)[0] if "." in part[1:] else part}
    return {s.lower() for s in out if len(s) >= MIN_LABEL and s.lower() not in GENERIC}


def words(text: str) -> set[str]:
    t = text.lower()
    return set(re.findall(r"[\w-]+", t)) | {w.rstrip(".") for w in re.findall(r"[\w.-]+", t)}


def section(heading: str) -> str:
    h = heading.replace("`", "").strip()
    m = NUMBERED.match(h)
    return f"§{m[1]}" if m else h[:48]


def paragraphs(text: str):
    """(section, [(line number, line)]) per paragraph, outside fenced code."""
    sec, para, fenced = "", [], False
    for i, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            h = HEADING.match(line)
            if not h and line.strip():
                para.append((i, line))
                continue
            if para:
                yield sec, para
            para = []
            if h:
                sec = section(h[1])
            continue
        if para:
            yield sec, para
        para = []
    if para:
        yield sec, para


class Index:
    def __init__(self, files: list[tuple[str, str]], graph: dict):
        self.repo = dict(files)
        self.by_suffix: dict[str, set[str]] = {}
        for p, _ in files:
            if p.endswith(".md"):
                continue
            parts = p.split("/")
            for i in range(len(parts)):
                self.by_suffix.setdefault("/".join(parts[i:]), set()).add(p)
        self.by_label: dict[str, dict[str, str]] = {}
        for n in graph.get("nodes", []):
            src, lab = str(n.get("source_file") or ""), str(n.get("label") or "").strip()
            if not src or not lab or n.get("file_type") == "rationale":
                continue
            # File nodes are matched by path; a label with a space is prose, not a name.
            if lab in (src, src.rsplit("/", 1)[-1]) or " " in lab or "/" in lab:
                continue
            key = last(lab)
            if len(key) >= MIN_LABEL:
                self.by_label.setdefault(key, {}).setdefault(src, lab)

    def resolve(self, name: str) -> tuple[dict[str, str | None], str] | None:
        """The candidate files (with the symbol, for a label match) and the word that matched."""
        if files := self.by_suffix.get(name):
            return {f: None for f in files}, ""
        if "/" in name or ("." in name and name.rsplit(".", 1)[-1].lower() in EXTS):
            return None
        # `state`, `plan`, `experiment`: a bare lowercase word is prose unless written `plan()`.
        if BARE.fullmatch(name):
            return None
        key = last(name)
        if len(key) >= MIN_LABEL and key in self.by_label:
            return dict(self.by_label[key]), key.lower()
        return None

    def pick(self, cands: list[str], hints: set[str]) -> str | None:
        """MIP-0085 §5.4: the one candidate whose repo or a path segment the paragraph names."""
        sets = {c: segs(c, self.repo.get(c, c.split("/")[0])) for c in cands}
        common = set.intersection(*sets.values())
        named = [c for c, s in sets.items() if (s - common) & hints]
        return named[0] if len(named) == 1 else None


def scan(root: Path, graph: dict, skip: set[str] | None = None) -> dict:
    files = tree(root)
    ix = Index(files, graph)
    docs: dict[str, dict] = {}
    code: dict[str, list[dict]] = {}
    stats = dict.fromkeys(("docs", "tokens", "matched", "multi", "resolved"), 0)
    for path, repo in files:
        rel = path if repo == "marola" else path.split("/", 1)[1]
        base = rel.rsplit("/", 1)[-1]
        if not path.endswith(".md") or base in GENERATED or path in (skip or set()):
            continue
        if not (rel.startswith("docs/") or base.lower().startswith("readme")):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        stats["docs"] += 1
        m = MIP.search(base) if path.startswith("docs/MIPs/") else None
        links, amb, seen = [], [], set()
        for sec, para in paragraphs(text):
            hints = None
            for i, line in para:
                for tok in TICK.findall(line):
                    stats["tokens"] += 1
                    for name in names(tok):
                        res = ix.resolve(name)
                        if res is None:
                            continue
                        cands, key = res
                        stats["matched"] += 1
                        chosen = next(iter(cands)) if len(cands) == 1 else None
                        if chosen is None:
                            stats["multi"] += 1
                            if hints is None:
                                hints = words("\n".join(x for _, x in para))
                            chosen = ix.pick(sorted(cands), hints - {key})
                            stats["resolved"] += chosen is not None
                        if chosen is None:
                            if (sec, tok) not in seen:
                                seen.add((sec, tok))
                                amb.append(
                                    {
                                        "section": sec,
                                        "token": tok,
                                        "line": i,
                                        "candidates": sorted(cands),
                                    }
                                )
                        elif (sec, tok, chosen) not in seen:
                            seen.add((sec, tok, chosen))
                            link = {"section": sec, "token": tok, "line": i}
                            links.append({**link, "file": chosen, "symbol": cands[chosen]})
                            code.setdefault(chosen, []).append(
                                {**link, "doc": path, "symbol": cands[chosen]}
                            )
        if links or amb:
            docs[path] = {"mip": m[0] if m else None, "links": links, "ambiguous": amb}
    stats["links"] = sum(len(d["links"]) for d in docs.values())
    stats["ambiguous"] = sum(len(d["ambiguous"]) for d in docs.values())
    subs = sorted({p.split("/", 1)[0] for p, r in files if r != "marola"})
    return {"stats": stats, "repos": subs, "docs": docs, "code": code}


def render(data: dict) -> str:
    by_mip: dict[str, set[str]] = {}
    pages: dict[str, set[str]] = {}
    for doc, d in data["docs"].items():
        files = {x["file"] for x in d["links"]}
        if files:
            (
                by_mip.setdefault(d["mip"], set()) if d["mip"] else pages.setdefault(doc, set())
            ).update(files)
    s = data["stats"]
    out = [
        "# Doc ↔ code",
        "",
        "<!-- Generated by marola-devkit's doc-code (MIP-0085 §5.4); pointer-sync rewrites it. -->",
        "",
        f"The code files each doc names in backticks, from {s['docs']} docs: {s['links']} links, "
        f"and {s['ambiguous']} names left ambiguous, which the routing bundle's `doc_code.json` "
        "lists with their candidates. `route docs` and `route code` answer from it.",
    ]
    for title, groups in (("MIPs", by_mip), ("Pages", pages)):
        if groups:
            out += ["", f"## {title}"]
        for key in sorted(groups):
            out += ["", f"### {key}", "", *[f"- `{f}`" for f in sorted(groups[key])]]
    return "\n".join(out) + "\n"


def rows(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("- "))


def write(data: dict, md: Path | None, js: Path | None) -> str | None:
    """Writes both files, or neither and says why: MIP-0085 §8's shrink guard."""
    text = render(data)
    if md:
        old = rows(md.read_text(encoding="utf-8")) if md.is_file() else 0
        new = rows(text)
        if new * 2 < old:
            return (
                f"{md} would drop from {old} rows to {new}, under half; refused (MIP-0085 §8). "
                "Delete it and rerun to accept the drop"
            )
        md.write_text(text, encoding="utf-8")
    if js:
        js.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return None


def check(data: dict, md: Path) -> str | None:
    if md.is_file() and md.read_text(encoding="utf-8") == render(data):
        return None
    return f"{md} is stale; rerun doc-code with --md {md}"


# ---------------------------------------------------------------- self-test

FIX_GRAPH = {
    "nodes": [
        {
            "id": "sw",
            "label": "Swimability",
            "source_file": "marola-app/core/src/main/scala/marola/scoring/Swimability.scala",
        },
        {
            "id": "score",
            "label": ".score()",
            "source_file": "marola-app/core/src/main/scala/marola/scoring/Swimability.scala",
        },
        {
            "id": "run",
            "label": "run()",
            "source_file": "marola-app/core/src/main/scala/x/Run.scala",
        },
        {
            "id": "c1",
            "label": "Client",
            "source_file": "marola-app/core/src/main/scala/x/Client.scala",
        },
        {"id": "c2", "label": "Client", "source_file": "marola-site/site/static/client.js"},
        {
            "id": "r",
            "label": "Why this exists.",
            "source_file": "marola-site/site/static/client.js",
        },
    ],
    "links": [],
}
FIX_FILES = {
    ".gitmodules": '[submodule "a"]\n\tpath = marola-app\n[submodule "s"]\n\tpath = marola-site\n',
    "scripts/release.sh": "",
    "marola-app/core/src/main/scala/marola/scoring/Swimability.scala": "",
    "marola-app/core/src/main/scala/x/Run.scala": "",
    "marola-app/core/src/main/scala/x/Client.scala": "",
    "marola-site/site/static/client.js": "",
    "marola-site/scripts/board-schema.sh": "",
    "marola-site/README.md": "# site\n\n`scripts/board-schema.sh` checks the board.\n",
    "marola-site/docs/4-reference.md": "# Reference\n\nRun `board-schema.sh`.\n",
    "docs/MIPs/MIP-0031-score.md": (
        "# MIP-0031\n\n## 5. Design\n\n`Swimability` keeps the score.\n\n"
        "`run()` is too short to name anything, and `score` is a word.\n\n"
        "A `Client` here could be either.\n\n"
        "In marola-site the `Client` draws.\n\n"
        "```\n`Swimability` inside a fence\n```\n"
    ),
}


def self_test() -> int:
    cases: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in FIX_FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        data = scan(root, FIX_GRAPH)

        def named(doc: str) -> list[str]:
            return [x["file"] for x in data["docs"].get(doc, {}).get("links", [])]

        mip = data["docs"].get("docs/MIPs/MIP-0031-score.md", {})
        links = {(x["token"], x["file"]) for x in mip.get("links", [])}
        amb = mip.get("ambiguous", [])
        site = "marola-site/scripts/board-schema.sh"
        sw = FIX_GRAPH["nodes"][0]["source_file"]
        cases.append(
            (
                "backtick_symbol_links",
                ("Swimability", sw) in links
                and mip.get("mip") == "MIP-0031"
                and any(x["doc"] == "docs/MIPs/MIP-0031-score.md" for x in data["code"].get(sw, []))
                and sum(1 for t, _ in links if t == "Swimability") == 1,
            )
        )
        cases.append(
            (
                "path_and_basename_link",
                named("marola-site/README.md") == [site]
                and named("marola-site/docs/4-reference.md") == [site],
            )
        )
        cases.append(("short_label_ignored", not any(t in ("run()", "score") for t, _ in links)))
        cases.append(
            (
                "ambiguous_kept_with_candidates",
                [(a["token"], a["candidates"]) for a in amb]
                == [
                    (
                        "Client",
                        [
                            "marola-app/core/src/main/scala/x/Client.scala",
                            "marola-site/site/static/client.js",
                        ],
                    )
                ],
            )
        )
        cases.append(
            (
                "paragraph_repo_disambiguates",
                ("Client", "marola-site/site/static/client.js") in links
                and ("Client", "marola-app/core/src/main/scala/x/Client.scala") not in links,
            )
        )
        md = root / "DOC-CODE.md"
        write(data, md, root / "doc_code.json")
        fresh = check(scan(root, FIX_GRAPH), md)
        (root / "docs/MIPs/MIP-0031-score.md").write_text("# MIP-0031\n", encoding="utf-8")
        stale = check(scan(root, FIX_GRAPH), md)
        cases.append(("check_fails_on_stale_file", fresh is None and stale is not None))
        before = md.read_text(encoding="utf-8") if md.is_file() else ""
        (root / "marola-site/README.md").write_text("# site\n", encoding="utf-8")
        refused = write(scan(root, FIX_GRAPH), md, None)
        cases.append(
            (
                "shrink_guard_refuses",
                rows(before) == 4
                and refused is not None
                and md.read_text(encoding="utf-8") == before,
            )
        )
    fails = [n for n, ok in cases if not ok]
    for name, ok in cases:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if fails:
        print(f"doc-code self-test: {len(fails)} failure(s)", file=sys.stderr)
        return 1
    print("doc-code self-test: ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="the umbrella checkout")
    ap.add_argument("--graph", type=Path, help="graph.json")
    ap.add_argument("--md", type=Path, help="write DOC-CODE.md here")
    ap.add_argument("--json", type=Path, help="write doc_code.json here")
    ap.add_argument("--check", type=Path, metavar="FILE", help="fail when FILE is stale")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.graph is None:
        ap.error("--graph is required")
    root = args.root.resolve()
    skip = {
        p.resolve().relative_to(root).as_posix()
        for p in (args.md, args.check)
        if p and p.resolve().is_relative_to(root)
    }
    data = scan(root, json.loads(args.graph.read_text(encoding="utf-8")), skip)
    s = data["stats"]
    print(
        f"doc-code: {s['docs']} docs, {s['tokens']} backticked tokens, {s['matched']} matched "
        f"({s['multi']} with several candidates, {s['resolved']} resolved by their paragraph); "
        f"{s['links']} links, {s['ambiguous']} ambiguous",
        file=sys.stderr,
    )
    if args.check:
        problem = check(data, args.check)
    elif args.md or args.json:
        problem = write(data, args.md, args.json)
    else:
        sys.stdout.write(render(data))
        problem = None
    if problem:
        print(f"doc-code: {problem}", file=sys.stderr)
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
