#!/usr/bin/env python3
"""The org's cross-repo wiring, parsed from every repo's workflows, pins and scripts (MIP-0076 §5.1).

Run in an umbrella checkout with its submodules; the devkit's tree is `.devkit`, else this script's.

wiring [--root DIR] [--devkit DIR] [--name NAME] [FILE]
    print the block, or rewrite it between FILE's wiring markers; a devkit git checkout with tags
    lets each reusable-workflow call be read at its own @ref, else it is fetched from GitHub
    (a tag is cached under ~/.cache/marola-wiring)
wiring --json
    the same facts as JSON, for the routing bundle (MIP-0084 §5.3)
wiring --check [FILE]
    fail on a stale block in FILE, an orphan or unreadable dispatch, or an artifact nobody reads that
    <root>/wiring.allow does not list as `<artifact>: <reason>`
wiring --lint-devkit [DIR]
    fail on a dispatch in the devkit's own workflows (DIR, default this script's repo) whose type
    wiring cannot read, or a type they listen for that none of them sends
wiring --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ORG = "marola-dev"
PINS = ("marola-image", "corpus.version", "resources.version")
START, END = "<!-- wiring:start -->", "<!-- wiring:end -->"
READERS = ("scripts/**/*", "justfile", "Dockerfile*", "docker-compose*.yml", "build.sbt")
USES = re.compile(rf"^{ORG}/([\w.-]+)/\.github/workflows/([\w.-]+)@([\w.-]+)$")
IMAGE = re.compile(rf"ghcr\.io/{ORG}/[\w.-]+")
IMAGE_REF = re.compile(
    r"""(?:image:\s*["']?(?:\$\{\w+:-)?|FROM\s+(?:--platform=\S+\s+)?)(""" + IMAGE.pattern + ")"
)
RELEASE = re.compile(r"gh release (?:upload|create)\b([^\n]*)")
# gh release flags that take a value; the first other argument is the tag, the rest are files.
VALUED = {"--repo", "-R", "--title", "-t", "--notes", "-n", "--notes-file", "-F", "--target"}
VALUED |= {"--discussion-category", "--notes-start-tag"}
ASSIGN = re.compile(r"""^\s*(\w+)=["']?([^"'\s]+)""", re.M)
ARRAY = re.compile(r"\b\w+\+?=\(([^)]*)\)")
TYPE = re.compile(r"[a-z][a-z0-9-]*")
SHVAR = re.compile(r"\$\{?(\w+)\}?")
WORKFLOW_DISPATCH = re.compile(r"/actions/workflows/[^/\s\"']+/dispatches")
EXPR = re.compile(r"\$\{\{\s*(env|inputs|github)\.([\w-]+)\s*\}\}")


class Wiring:
    def __init__(self) -> None:
        self.artifacts: dict[str, dict] = {}
        self.dispatch: dict[str, dict[str, list[str]]] = {}
        self.bumps: dict[str, list[str]] = {}
        self.deploys: list[tuple[str, str]] = []
        self.devkit: dict[str, list[str]] = {}
        self.calls: dict[str, dict[str, list[str]]] = {}
        self.unread: list[str] = []
        self.contracts: dict[str, dict] = {}
        self.bad_contracts: list[str] = []

    def art(self, key: str, repo: str = "", match: str = "") -> dict:
        a = self.artifacts.setdefault(key, {"pub": [], "pin": [], "read": [], "repos": set()})
        a["repos"].add(repo)
        a.setdefault("match", match)
        return a


def add(xs: list[str], x: str) -> None:
    if x not in xs:
        xs.append(x)


def sub(s: str, env: dict, inputs: dict) -> str:
    def expr(m: re.Match) -> str:
        ctx, name = m.groups()
        if ctx == "github":
            known = {"repository_owner": ORG, "repository": env.get("GITHUB_REPOSITORY")}
            return known.get(name) or m[0]
        return str((env if ctx == "env" else inputs).get(name, m[0]))

    s = EXPR.sub(expr, str(s))
    return SHVAR.sub(lambda m: str(env.get(m[1], m[0])), s)


def lines(text: str) -> str:
    return "\n".join(x for x in text.splitlines() if not x.lstrip().startswith(("#", "//")))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def release_files(run: str) -> list[str]:
    out: list[str] = []
    for rest in RELEASE.findall(run.replace("\\\n", " ")):
        lex = shlex.shlex(rest, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        try:
            toks = list(lex)
        except ValueError:
            toks = rest.split()
        args: list[str] = []
        skip = False
        for tok in toks:
            if set(tok) <= set(lex.punctuation_chars):
                break
            if not skip and not tok.startswith("-"):
                args.append(tok)
            skip = not skip and tok in VALUED
        out += args[1:]
    return out


def fetch(url: str) -> str | None:
    """url's text, None on a 404; any other failure raises OSError."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def at_ref(repo: str, ref: str, file: str) -> str | None:
    cached = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    cached = cached / "marola-wiring" / repo / ref / file
    tagged = re.fullmatch(r"v\d+\.\d+\.\d+", ref) and repo not in (".", "..")
    if tagged and cached.is_file():  # a branch moves, so only a tag is cached
        return read(cached)
    text = fetch(f"https://raw.githubusercontent.com/{ORG}/{repo}/{ref}/.github/workflows/{file}")
    if tagged and text is not None:
        with contextlib.suppress(OSError):
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
    return text


def listed(d: Path, text: str) -> tuple[str, list[str]] | None:
    """The first file text names that holds only owner/repo lines, and those repos."""
    for tok in re.findall(r"[\w.-]*(?:/[\w.-]+)+", text):
        parts = [x for x in tok.split("/") if x]
        for i in range(len(parts) * (".." not in parts)):
            names = lines(read(d.joinpath(*parts[i:]))).split()
            if names and all(re.fullmatch(r"[\w.-]+/[\w.-]+", n) for n in names):
                return "/".join(parts[i:]), [n.split("/")[1] for n in names]
    return None


def warn(msg: str) -> None:
    print(f"wiring: warning: {msg}", file=sys.stderr)


def git(d: Path, *args: str) -> str | None:
    if not (d / ".git").exists():  # never the enclosing repo's answer
        return None
    r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else None


def load(text: str, where: object) -> dict:
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"wiring: {where}: {e}") from None


def version(d: Path) -> str:
    if v := git(d, "describe", "--tags", "--always"):
        return v.strip()
    m = re.search(r"marola-devkit-([\d.]+)", str(d.resolve()))
    return f"v{m[1]}" if m else "its working tree"


def repos(root: Path, name: str, devkit: Path | None) -> list[tuple[str, Path]]:
    out = [(name, root)]
    for path in re.findall(r"^\s*path\s*=\s*(\S+)", read(root / ".gitmodules"), re.M):
        if not (root / path / ".git").exists():
            raise SystemExit(f"wiring: {path} is not checked out (git submodule update --init)")
        out.append((Path(path).name, root / path))
    if devkit is None:
        devkit = root / ".devkit"
        devkit = devkit if devkit.is_dir() else Path(__file__).resolve().parents[1]
    return [(n, p) for n, p in out if p.is_dir()] + [("marola-devkit", devkit)]


def triggers(doc: dict) -> dict:
    on = doc.get("on", doc.get(True)) or {}
    if isinstance(on, str | list):
        on = {k: None for k in ([on] if isinstance(on, str) else on)}
    return {k: v or {} for k, v in on.items()}


def gated_paths(doc: dict) -> set[str]:
    """Paths a `dorny/paths-filter` filter lists, when that filter's output gates a job's `if:`."""
    jobs = doc.get("jobs") or {}
    ifs = "\n".join(str(j.get("if", "")) for j in jobs.values())
    out: set[str] = set()
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if not str(step.get("uses", "")).startswith("dorny/paths-filter"):
                continue
            filters = (step.get("with") or {}).get("filters")
            if isinstance(filters, str):  # a file path stays a str and is not read
                filters = load(filters, f"{name}'s paths-filter")
            if not isinstance(filters, dict):
                continue
            for o, v in (job.get("outputs") or {}).items():
                gates = re.search(
                    rf"\bneeds\.{re.escape(name)}\.outputs\.{re.escape(o)}(?![\w-])", ifs
                )
                for sid, f in re.findall(r"steps\.([\w-]+)\.outputs\.([\w-]+)", str(v)):
                    if gates and sid == step.get("id") and isinstance(filters.get(f), list):
                        out |= {p for p in filters[f] if isinstance(p, str)}
    return out


def pin_triggers(on: dict, doc: dict) -> set[str]:
    if "push" not in on:
        return set()
    push, pins = on["push"], set(PINS + ("flake.lock",))
    if push.get("paths"):
        return pins & set(push["paths"])
    # a push filtered to tags alone never runs on a branch
    if "main" not in (push.get("branches") or ([] if push.get("tags") else ["main"])):
        return set()
    return (pins & gated_paths(doc)) - set(push.get("paths-ignore") or [])


def when(on: dict) -> str:
    push = on.get("push", {})
    if push.get("tags"):
        return f"on a `{push['tags'][0]}` tag"
    if "push" in on:
        paths = push.get("paths") or []
        touching = " touching " + ", ".join(f"`{p}`" for p in paths) if paths else ""
        touching = f" touching {len(paths)} paths" if len(paths) > 3 else touching
        return f"on a push to `{(push.get('branches') or ['main'])[0]}`{touching}"
    if "schedule" in on:
        return "on a schedule"
    return "by hand" if set(on) == {"workflow_dispatch"} else ""


def site_of(repo_dir: Path, text: str) -> str:
    if m := re.search(r"""echo\s+["']?([\w.-]+\.[a-z]+)["']?\s*>\s*\S*CNAME""", text):
        return m[1]
    for f in ("mkdocs/mkdocs.yml", "mkdocs.yml"):
        if m := re.search(r"^site_url:\s*https?://([^/\s]+)", read(repo_dir / f), re.M):
            return m[1]
    return "?"


def effects(doc: dict, repo: str, inputs: dict, callee, d: Path) -> list[tuple]:
    """(kind, value, via) for what a workflow sends, publishes, deploys or writes elsewhere."""
    out: list[tuple] = []
    text = yaml.safe_dump(doc)
    for job in (doc.get("jobs") or {}).values():
        env = {
            "GITHUB_REPOSITORY": f"{ORG}/{repo}",
            **(doc.get("env") or {}),
            **(job.get("env") or {}),
        }
        if m := USES.match(str(job.get("uses", ""))):
            target, note = callee(m[1], m[2], m[3])
            if target:
                defaults = triggers(target).get("workflow_call", {}).get("inputs") or {}
                given = {k: v.get("default", "") for k, v in defaults.items()}
                given |= {k: sub(v, env, inputs) for k, v in (job.get("with") or {}).items()}
                via = f"`{m[2]}@{m[3]}`{note}"
                out += [(k, v, via) for k, v, _ in effects(target, repo, given, callee, d)]
            if m[1] == "marola-devkit":
                out.append(("ref", (m[2], m[3]), ""))
            continue
        checkouts: dict[str, tuple[str, str]] = {}
        for step in job.get("steps") or []:
            senv = {**env, **(step.get("env") or {})}
            uses, w = str(step.get("uses", "")), step.get("with") or {}
            if uses.startswith("actions/checkout") and w.get("repository"):
                name = sub(w["repository"], senv, inputs).rsplit("/", 1)[-1]
                checkouts[str(w.get("path", "."))] = (name, str(w.get("ref", "")))
            if uses.startswith("docker/build-push-action") and (
                m := IMAGE.search(sub(env.get("IMAGE", ""), senv, inputs))
            ):
                out.append(("image", m[0], ""))
            if uses.startswith("actions/deploy-pages"):
                out.append(("deploy", text, ""))
            run = step.get("run")
            if not run:
                continue
            assigned = dict(ASSIGN.findall(run))
            sent = [t for t in re.findall(r"event_type=([^\s\"']*)", sub(run, senv, inputs)) if t]
            if "/dispatches" in run:
                sent += [sub(v, {}, inputs) for k, v in senv.items() if "EVENT" in k]
                # v0.7.0's notify-umbrella builds its types in an array (`events+=(…)`, #69).
                sent += [w for a in ARRAY.findall(run) for w in a.split() if TYPE.fullmatch(w)]
                repo_dispatch = "/dispatches" in WORKFLOW_DISPATCH.sub("", run)
                # v0.15.0's JSON literal (#163); a workflow_dispatch call has no event type.
                if repo_dispatch and not any(t and "$" not in t for t in sent):
                    out.append(("unread-dispatch", "", ""))
            out += [("send", t, "") for t in sent if "$" not in t]
            for asset in release_files(run):
                name = sub(asset, assigned, {}).rsplit("/", 1)[-1]
                out.append(("asset", SHVAR.sub("<tag>", name), ""))
            live = "\n".join(ln for ln in run.splitlines() if "--self-test" not in ln)
            # A trap: any `<name>-push.sh` call is taken as publishing branch `<name>`.
            if branches := re.findall(r"([\w-]+)-push\.sh\b", live):
                for b in branches:
                    owner = next((r for r, ref in checkouts.values() if ref == b), repo)
                    out.append(("branch", (b, owner), ""))
                continue
            scripts = re.findall(r"(?<![\w./-])(?:\./)?(scripts/[\w.-]+\.sh)\b", live)
            prs = "\n".join([run, *(lines(read(d / f)) for f in scripts)])
            if m := re.search(
                r"gh pr create\b.*?\s(?:--repo|-R)[\s=]+(\"[^\"]*\"|'[^']*'|\S+)", prs, re.S
            ):
                files = sorted(
                    {
                        Path(f).name
                        for f in " ".join(re.findall(r"git add\s+(.+)", prs)).split()
                        if not f.startswith("-") and f != "."
                    }
                )
                into = sub(m[1].strip("\"'"), senv, inputs).rsplit("/", 1)[-1]
                if "$" not in into:
                    out.append(("pr", (into, files), ""))
                elif each := listed(d, prs):
                    out.append(("prs", each, ""))
                else:
                    warn(f"{repo}: PRs into {m[1]}, which no owner/repo list names; not shown")
            elif m := re.search(r"(?:git -C (\S+) |cd (\S+)[^\n]*\n(?:.*\n)*?\s*git )push\b", run):
                target = checkouts.get((m[1] or m[2]).strip("\"'"))
                if target:
                    out.append(("push", target[0], ""))
    return [(k, v, via) for k, v, via in out if k not in ("pr", "push") or v[0] != repo]


def scan(root: Path, name: str = "marola", devkit: Path | None = None) -> Wiring:
    w, rs = Wiring(), repos(root, name, devkit)
    dirs = dict(rs)
    docs: dict[tuple[str, str], dict] = {}
    for repo, d in rs:
        for f in sorted((d / ".github" / "workflows").glob("*.y*ml")):
            docs[(repo, f.name)] = load(read(f), f)
    cache: dict[tuple[str, str, str], tuple[dict | None, str]] = {}

    def callee(repo: str, file: str, ref: str) -> tuple[dict | None, str]:
        key, d = (repo, file, ref), dirs.get(repo)
        if key in cache:
            return cache[key]
        cache[key] = (None, "")
        if d and git(d, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"):
            text = git(d, "show", f"{ref}:.github/workflows/{file}")
        else:  # under Nix .devkit is a store path with no .git
            try:
                text = at_ref(repo, ref, file)
            except OSError as e:
                text = None
                if d and (repo, file) in docs:
                    warn(f"cannot fetch {repo}@{ref} ({e}); reading {file} at {version(d)}")
                    cache[key] = (docs[(repo, file)], f" (resolved at {version(d)})")
        if text:
            cache[key] = (load(text, f"{repo} {ref}:{file}"), "")
        if cache[key][0] is None:
            warn(f"{repo}/{file}@{ref} not found; its effects are not shown")
        return cache[key]

    for (repo, file), doc in docs.items():
        on = triggers(doc)
        if set(on) == {"workflow_call"}:
            continue
        label = f"{repo} `{file}`"
        for t in on.get("repository_dispatch", {}).get("types") or []:
            add(w.dispatch.setdefault(t, {"sent": [], "listen": []})["listen"], label)
        for pin in pin_triggers(on, doc):
            add(w.bumps.setdefault(f"{repo} `{pin}`", []), label)
        repo_dir = dict(rs)[repo]
        for kind, v, via in effects(doc, repo, {}, callee, dirs[repo]):
            parts = [p for p in (via, when(on)) if p]
            pub = label + (f" ({', '.join(parts)})" if parts else "")
            if kind == "send":
                add(w.dispatch.setdefault(v, {"sent": [], "listen": []})["sent"], pub)
            elif kind == "unread-dispatch":
                add(w.unread, label)
            elif kind == "image":
                add(w.art(f"`{v}` image", repo, v)["pub"], pub)
            elif kind == "asset":
                add(w.art(f"`{v}` release asset", repo)["pub"], pub)
            elif kind == "branch":
                b, owner = v
                key = f"`{b}` branch" + (f" of {owner}" if owner != repo else "")
                add(w.art(key, owner, b)["pub"], pub)
            elif kind == "pr":
                a = w.art(f"PRs into {v[0]}: " + ", ".join(f"`{f}`" for f in v[1]), v[0])
                add(a["pub"], pub)
                add(a["read"], v[0])
            elif kind == "prs":
                a = w.art(f"PRs into each repo in `{v[0]}`", repo)
                add(a["pub"], pub)
                for r in v[1]:
                    add(a["read"], r)
            elif kind == "push":
                a = w.art(f"pushes to {v}", v)
                add(a["pub"], pub)
                add(a["read"], v)
            elif kind == "deploy":
                w.deploys.append((label, site_of(repo_dir, v)))
            elif kind == "ref":
                add(w.calls.setdefault(v[0], {}).setdefault(v[1], []), repo)
    for repo, d in rs:
        files = sorted({f for g in READERS for f in d.glob(g) if f.is_file()})
        texts = {f.relative_to(d).as_posix(): lines(read(f)) for f in files}
        if repo == "marola-devkit":
            texts.pop("scripts/wiring.py", None)  # its fixture names every idiom
        for f in sorted((d / ".github" / "workflows").glob("*.y*ml")):
            texts[f".github/workflows/{f.name}"] = lines(read(f))
        for rel, text in texts.items():
            for img in IMAGE_REF.findall(text):
                if (key := f"`{img}` image") in w.artifacts:
                    add(w.artifacts[key]["read"], f"{repo} `{Path(rel).name}`")
            for key, a in w.artifacts.items():
                b = a["match"]
                if " branch" not in key or rel.endswith(f"{b}-push.sh"):
                    continue
                names = {b} | {v for v, x in ASSIGN.findall(text) if x == b}
                if any(
                    re.search(r"\bgit\b[^\n]*\bfetch\b", ln)
                    and any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", ln) for n in names)
                    for ln in text.splitlines()
                ):
                    add(
                        a["read"],
                        f"{repo} `{rel if not rel.startswith('.github') else Path(rel).name}`",
                    )
        for pin in PINS:
            if not (d / pin).is_file():
                continue
            readers = [r for r, t in texts.items() if pin in t and not r.startswith(".github")]
            content = read(d / pin)
            producers = {
                p
                for r in readers
                for p in re.findall(rf"{ORG}/([\w.-]+)/releases/download", texts[r])
            }
            for key, a in w.artifacts.items():
                is_img = bool(a["match"]) and a["match"] in content and key.endswith(" image")
                if is_img or (key.endswith("release asset") and a["repos"] & producers):
                    add(a["pin"], f"{repo} `{pin}`")
                    for r in readers:
                        add(a["read"], f"{repo} `{r}`")
        lock = json.loads(read(d / "flake.lock") or "{}")
        for node in lock.get("nodes", {}).values():
            if (node.get("original") or {}).get("repo") == "marola-devkit":
                add(
                    w.devkit.setdefault(repo, []),
                    f"`flake.lock` {node['original'].get('ref', '?')}",
                )
        if not w.devkit.get(repo) and (
            m := re.search(rf"github:{ORG}/marola-devkit/([\w.-]+)", read(d / "flake.nix"))
        ):
            add(w.devkit.setdefault(repo, []), f"`flake.nix` {m[1]}")
        settings = json.loads(read(d / ".claude" / "settings.json") or "{}")
        for m in (settings.get("extraKnownMarketplaces") or {}).values():
            if (m.get("source") or {}).get("repo") == f"{ORG}/marola-devkit":
                add(w.devkit.setdefault(repo, []), f"marketplace {m['source'].get('ref', '?')}")
    link_contracts(w, rs, root)
    return w


# A contract is a data file copied byte for byte into another repo (MIP-0085 §5.3): change one and
# the other drifts. Lock files, dot-directories (workflows synced by the devkit, vendored skills) and
# anything under node_modules are copies by design, not contracts.
DATA = re.compile(r"\.(json|ya?ml|csv|tsv|sql|proto|avsc|xsd|graphql)$")
NOT_DATA = {"package.json", "package-lock.json"}
TEST_DIRS = {"test", "tests", "fixtures", "testdata"}


def data_files(d: Path, skip: list[str]) -> list[str]:
    listed_ = git(d, "ls-files", "-z")
    rels = (
        [f for f in listed_.split("\0") if f]
        if listed_ is not None
        else [p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()]
    )
    return [
        r
        for r in rels
        if DATA.search(r)
        and not r.endswith(".lock")
        and Path(r).name not in NOT_DATA
        and not any(part.startswith(".") or part == "node_modules" for part in r.split("/"))
        and not any(r == s or r.startswith(f"{s}/") for s in skip)
    ]


def link_contracts(w: Wiring, rs: list[tuple[str, Path]], root: Path) -> None:
    dirs = dict(rs)
    subs = [p.relative_to(root).as_posix() for _, p in rs if p != root and p.is_relative_to(root)]
    groups: dict[tuple[str, str], list[str]] = {}
    for repo, d in rs:
        for rel in data_files(d, subs if d == root else []):
            digest = hashlib.sha1((d / rel).read_bytes()).hexdigest()  # noqa: S324 - not security
            groups.setdefault((Path(rel).name, digest), []).append(f"{repo}/{rel}")
    for keys in groups.values():
        if len({k.split("/", 1)[0] for k in keys}) > 1:
            # The canonical file is the first by path, a test copy never: a `contract` line says otherwise.
            first, *rest = sorted(keys, key=lambda k: (bool(TEST_DIRS & set(k.split("/"))), k))
            w.contracts[first] = {"copies": rest, "note": "identical"}
    for ln in read(root / "wiring.allow").splitlines():
        if not (m := re.match(r"\s*contract\s+([^:]+):(.*)$", ln)):
            continue
        paths, reason = m[1].split(), m[2].strip()
        missing = [
            p
            for p in paths
            if not (dirs.get(p.split("/", 1)[0], root / "-") / p.partition("/")[2]).is_file()
        ]
        if missing:
            w.bad_contracts.append(f"wiring.allow: contract `{missing[0]}` names no file")
        elif len(paths) < 2 or not reason:
            w.bad_contracts.append(f"wiring.allow: contract `{paths[0]}` needs a copy and a reason")
        else:
            for p in paths:
                w.contracts.pop(p, None)
            w.contracts[paths[0]] = {"copies": paths[1:], "note": reason}


def render(w: Wiring) -> str:
    def cell(xs) -> str:
        return ", ".join(xs) or "—"

    out = ["| Artifact | Published by | Pinned in | Read by |", "|---|---|---|---|"]
    kinds = (" image", "release asset", " branch", "PRs into", "pushes to")
    for key, a in sorted(
        w.artifacts.items(), key=lambda kv: ([k in kv[0] for k in kinds].index(True), kv[0])
    ):
        out.append(f"| {key} | {cell(a['pub'])} | {cell(a['pin'])} | {cell(a['read'])} |")
    pins = "; ".join(f"{r} {', '.join(v)}" for r, v in w.devkit.items()) or "—"
    calls = "; ".join(
        f"`{f}` " + ", ".join(f"{ref} ({' '.join(rs)})" for ref, rs in sorted(refs.items()))
        for f, refs in sorted(w.calls.items())
    )
    out += [f"| a marola-devkit tag | marola-devkit | {pins} | {calls or '—'} |", ""]
    out += ["| Dispatch | Sent by | Triggers |", "|---|---|---|"]
    for t, d in sorted(w.dispatch.items()):
        out.append(f"| `{t}` | {cell(d['sent'])} | {cell(d['listen'])} |")
    out += ["", "| Pin bump | Workflow |", "|---|---|"]
    out += [f"| {p} | {cell(wf)} |" for p, wf in sorted(w.bumps.items())]
    out += ["", "| Deploy | Site |", "|---|---|"]
    out += [f"| {wf} | {site} |" for wf, site in w.deploys]

    def at(path: str) -> str:
        repo, _, rel = path.partition("/")
        return f"{repo} `{rel}`"

    out += ["", "| Contract | Copied to | Kept |", "|---|---|---|"]
    out += [
        f"| {at(c)} | {', '.join(map(at, v['copies']))} | {v['note']} |"
        for c, v in sorted(w.contracts.items())
    ]
    return "\n".join(out) + "\n"


def to_json(w: Wiring) -> str:
    arts = {k: {**a, "repos": sorted(a["repos"])} for k, a in w.artifacts.items()}
    keys = ("dispatch", "bumps", "deploys", "devkit", "calls", "contracts")
    return json.dumps(
        {"artifacts": arts, **{k: getattr(w, k) for k in keys}}, indent=1, sort_keys=True
    )


def write_block(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition(START)
    _, sep2, tail = rest.partition(END)
    if not (sep and sep2):
        raise SystemExit(f"wiring: {path} has no {START} … {END} markers")
    path.write_text(f"{head}{START}\n\n{block}\n{END}{tail}", encoding="utf-8")


UNREAD = "a dispatch whose type wiring cannot read"
ORPHAN = "dispatch `%s` is listened for but never sent"


def lint_devkit(d: Path) -> list[str]:
    """The devkit's own dispatch problems, its calls of itself read from DIR's working tree."""
    files = sorted((d / ".github" / "workflows").glob("*.y*ml"))
    docs = {f.name: load(read(f), f) for f in files}
    for doc in docs.values():
        for job in (doc.get("jobs") or {}).values():
            if (u := str(job.get("uses", ""))).startswith("./.github/workflows/"):
                job["uses"] = f"{ORG}/marola-devkit/{u[2:]}@HEAD"

    calls = {
        f: {
            m[2]
            for job in (doc.get("jobs") or {}).values()
            if (m := USES.match(str(job.get("uses", "")))) and m[1] == "marola-devkit"
        }
        for f, doc in docs.items()
    }

    def cyclic(f: str, path: tuple[str, ...] = ()) -> bool:
        return f in path or any(cyclic(g, (*path, f)) for g in calls.get(f, ()))

    def callee(repo: str, file: str, ref: str) -> tuple[dict | None, str]:
        return (docs.get(file) if repo == "marola-devkit" else None), ""

    sent, listen, problems = set(), set(), []
    for file, doc in docs.items():
        on = triggers(doc)
        if set(on) == {"workflow_call"}:
            continue
        if cyclic(file):  # effects() would recurse forever; GitHub rejects such a call too
            add(problems, f"marola-devkit `{file}`: a reusable-workflow call cycle")
            continue
        listen |= set(on.get("repository_dispatch", {}).get("types") or [])
        for kind, v, _ in effects(doc, "marola-devkit", {}, callee, d):
            if kind == "send":
                sent.add(v)
            elif kind == "unread-dispatch":
                add(problems, f"marola-devkit `{file}`: {UNREAD}")
    return problems + [ORPHAN % t for t in sorted(listen - sent)]


def check(w: Wiring, block: str, path: Path | None, allow: str) -> list[str]:
    out: list[str] = []
    if path:
        _, s, rest = read(path).partition(START)
        inner, e, _ = rest.partition(END)
        if not (s and e) or inner != f"\n\n{block}\n":
            out.append(f"{path}'s wiring block is stale: run `just wiring {path}`")
    for t, d in sorted(w.dispatch.items()):
        if not (d["sent"] and d["listen"]):
            out.append(
                f"dispatch `{t}` is sent but never listened for" if d["sent"] else ORPHAN % t
            )
    out += [f"{label}: {UNREAD}" for label in sorted(w.unread)]
    names = {k.replace("`", ""): a for k, a in w.artifacts.items()}
    allowed = set()
    out += w.bad_contracts
    for ln in map(str.strip, allow.splitlines()):
        if not ln or ln.startswith("#") or ln.startswith("contract "):
            continue
        # an artifact name can hold ": " itself (PRs into …: files), so match the known ones first
        name = next((n for n in names if ln.startswith(f"{n}:")), ln.partition(":")[0])
        if not ln[len(name) + 1 :].strip():
            out.append(f"wiring.allow: `{name}` needs a reason")
        elif name not in names:
            out.append(f"wiring.allow: `{name}` names no artifact")
        else:
            allowed.add(name)
    for n, a in names.items():
        if not a["read"] and n not in allowed:
            out.append(
                f"`{n}` has no reader; list it in wiring.allow with a reason if that is intended"
            )
    return out


CO = "      - uses: actions/checkout@v7\n        with: {repository: %s, path: %s%s}\n"
CHANGES = (
    "  changes:\n    outputs:\n      %s: ${{ steps.filter.outputs.%s }}\n    steps:\n"
    "      - uses: dorny/paths-filter@v4\n        id: filter\n        with:\n"
    "          filters: |\n            %s:\n              - 'Dockerfile'\n              - %s\n"
)
FIXTURE = {
    ".gitmodules": "".join(
        f'[submodule "{r}"]\n\tpath = {r}\n'
        for r in ("marola-app", "marola-site", "marola-corpus", "marola-ml")
    ),
    "mkdocs/mkdocs.yml": "site_name: x\nsite_url: https://docs.marola.dev/\n",
    "scripts/fetch-api-docs.sh": 'API_DOCS_BRANCH="api-docs"\ngit -C "$tmp" fetch -q "$url" "$API_DOCS_BRANCH"\n',
    ".github/workflows/pointer-sync.yml": "on:\n  repository_dispatch:\n    types: [submodule-updated, submodule-docs-updated]\njobs: {}\n",
    ".github/workflows/release.yml": 'on: {push: {tags: [v*]}}\njobs:\n  r:\n    steps:\n      - run: gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --verify-tag --title "x $TAG"\n',
    ".github/workflows/docs.yml": "on: {push: {paths: [flake.lock]}}\njobs:\n  d:\n    steps:\n      - uses: actions/deploy-pages@v5\n",
    ".devkit/.github/workflows/notify-umbrella.yml": "on:\n  workflow_call:\n    inputs:\n      event-type: {type: string, default: submodule-docs-updated}\njobs:\n  dispatch:\n    steps:\n      - env: {EVENT_TYPE: '${{ inputs.event-type }}'}\n        run: curl https://api.github.com/repos/$UMBRELLA/dispatches -d x\n",
    ".devkit/.github/workflows/api-docs.yml": "on: {workflow_call: {inputs: {devkit-ref: {type: string}}}}\njobs:\n  publish:\n    steps:\n      - run: bash .devkit-checkout/scripts/api-docs-push.sh out url sha\n",
    "marola-site/.github/workflows/notify-umbrella.yml": "on: {push: {branches: [main], paths: [README.md, 'docs/**']}}\njobs:\n  n:\n    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.3.1\n",
    "marola-site/.github/workflows/site.yml": 'on:\n  push: {paths: [marola-image]}\n  repository_dispatch: {types: [site-data-updated]}\njobs:\n  b:\n    steps:\n      - run: git fetch --depth=1 origin site-data\n      - run: echo "marola.dev" > site/dist/CNAME\n      - uses: actions/deploy-pages@v5\n',
    "marola-site/marola-image": "ghcr.io/marola-dev/marola-app:jvm-8a29976@sha256:00\n",
    "marola-site/scripts/board-schema.sh": '# marola-image is read here\nref="$(cat "$root/marola-image")"\n',
    "marola-corpus/.github/workflows/api-docs.yml": "on: {push: {branches: [main]}}\njobs:\n  a:\n    uses: marola-dev/marola-devkit/.github/workflows/api-docs.yml@v9.9.9\n",
    "marola-site/.github/workflows/odd.yml": "on: {workflow_dispatch: {}}\njobs:\n  r:\n    uses: marola-dev/marola-app/.github/workflows/reusable.yml@main\n  x:\n    uses: marola-dev/marola-infra/.github/workflows/x.yml@v1\n  d:\n    env: {EVENT_TYPE: '${{ inputs.ev }}'}\n    steps:\n      - run: curl https://api.github.com/repos/x/dispatches -d y\n",
    "marola-site/.github/workflows/gemini.yml": "on: {pull_request: {}}\njobs:\n  g:\n    uses: marola-dev/marola-devkit/.github/workflows/gemini-review.yml@v0.5.0\n",
    "marola-corpus/.github/workflows/release.yml": 'on: {push: {tags: [\'v*\']}}\njobs:\n  t:\n    steps:\n      - run: |\n          file=".tmp/marola-corpus-$TAG.tar.gz"\n          gh release upload "$TAG" "$file"\n',
    "marola-app/.github/workflows/docker.yml": "on: {push: {branches: [main], paths: [corpus.version]}}\nenv: {IMAGE: 'ghcr.io/${{ github.repository_owner }}/marola-app'}\njobs:\n  jvm:\n    steps:\n      - uses: docker/build-push-action@v7\n",
    "marola-app/.github/workflows/api-docs.yml": "on: {push: {branches: [main]}}\njobs:\n  a:\n    uses: marola-dev/marola-devkit/.github/workflows/api-docs.yml@v0.4.1\n    with: {devkit-ref: v0.4.1}\n",
    "marola-app/.github/workflows/notify-umbrella.yml": "on: {push: {branches: [main]}}\njobs:\n  n:\n    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.6.0\n    with: {event-type: submodule-updated}\n",
    "marola-app/.github/workflows/ci.yml": "on: {push: {branches: [main]}}\njobs:\n  c:\n    steps:\n"
    + CO % ("marola-dev/marola-site", "site-data", ", ref: site-data")
    + "      - run: |\n          git -C site-data commit -m x\n          scripts/site-data-push.sh site-data\n      - run: gh api repos/marola-dev/marola-site/dispatches -f event_type=site-data-updated\n",
    "marola-app/.github/workflows/ingest.yml": "on: {schedule: [{cron: '0 4 * * *'}]}\njobs:\n  i:\n    steps:\n"
    + CO % ("marola-dev/marola-oods", "oods", "")
    + "      - run: |\n          git -C oods commit -qm ingest\n          git -C oods push\n",
    "marola-app/docker-compose.yml": "services:\n  ollama-local:\n    image: ghcr.io/marola-dev/marola-ml:local\n",
    "marola-app/flake.lock": json.dumps(
        {"nodes": {"marola-devkit": {"original": {"repo": "marola-devkit", "ref": "v0.4.1"}}}}
    ),
    "marola-ml/.github/workflows/docker-local.yml": "on: {push: {branches: [main]}}\nenv: {IMAGE: ghcr.io/marola-dev/marola-ml}\njobs:\n  b:\n    steps:\n      - uses: docker/build-push-action@v7\n",
    "marola-ml/.github/workflows/compile-prompt.yml": "on: {workflow_dispatch: {}}\nenv: {APP_REPO: marola-dev/marola-app}\njobs:\n  c:\n    steps:\n"
    + CO % ("'${{ env.APP_REPO }}'", "app", "")
    + '      - run: |\n          cd app\n          git add core/src/main/resources/recommendation_prompt.json core/src/main/resources/review_prompt.json\n          git push -q origin "$BRANCH"\n          gh pr create --repo "$APP_REPO" --base main\n',
    "marola-ml/corpus.version": "v0.2.0\n",
    "marola-ml/justfile": "# corpus.version is bumped by its release\nfetch:\n    echo x\n",
    "marola-site/.github/workflows/self-pr.yml": 'on: {workflow_dispatch: {}}\njobs:\n  a:\n    steps:\n      - run: gh pr create --repo "$GITHUB_REPOSITORY" --base main\n  b:\n    steps:\n      - run: gh pr create -R "${{ github.repository }}" --base main\n',
    "marola-corpus/.github/workflows/docker.yml": "on: {push: {branches: [main]}}\nenv: {IMAGE: ghcr.io/marola-dev/marola-corpus}\njobs:\n  b:\n    steps:\n      - uses: docker/build-push-action@v7\n",
    "marola-site/docker-compose.yml": 'services:\n  c:\n    image: "ghcr.io/marola-dev/marola-corpus:1"\n',
    "marola-ml/Dockerfile": "FROM --platform=$BUILDPLATFORM ghcr.io/marola-dev/marola-corpus:1\n",
    "marola-ml/docker-compose.yml": "services:\n  c:\n    image: ${CORPUS_IMAGE:-ghcr.io/marola-dev/marola-corpus:1}\n",
    ".devkit/.github/workflows/bump-consumers.yml": 'on: {workflow_dispatch: {}}\njobs:\n  b:\n    steps:\n      - run: scripts/bump-consumers.sh "$VERSION"\n',
    ".devkit/scripts/bump-consumers.sh": 'repos() { grep -v "^#" "$root/.github/consumers.txt"; }\nwhile read -r repo; do\n  gh pr create -R "$repo" --head x\ndone < <(repos)\n',
    ".devkit/.github/consumers.txt": "# pin the devkit\nmarola-dev/marola\nmarola-dev/marola-app\n",
    ".devkit/.github/workflows/ping.yml": 'on: {workflow_call: {}}\njobs:\n  p:\n    steps:\n      - run: curl https://api.github.com/repos/x/dispatches -d \'{"event_type":"x"}\'\n',
    "marola-ml/.github/workflows/ping.yml": "on: {push: {branches: [main]}}\njobs:\n  a:\n    uses: marola-dev/marola-devkit/.github/workflows/ping.yml@v0.3.1\n  b:\n    uses: marola-dev/marola-devkit/.github/workflows/ping.yml@v0.3.1\n",
    "marola-ml/scripts/corpus-fetch.sh": 'pin="$(<"$root/corpus.version")"\nbase="https://github.com/marola-dev/marola-corpus/releases/download"\n',
    "marola-ml/.github/workflows/ci.yml": "on: {push: {branches: [main]}, pull_request: {}}\njobs:\n"
    + CHANGES % ("docker", "docker", "docker", "'corpus.version'")
    + "  jvm:\n    needs: [changes]\n    if: needs.changes.outputs.docker == 'true' || github.event_name == 'workflow_dispatch'\n    steps: []\n",
    "marola-corpus/.github/workflows/ci.yml": "on: {push: {branches: [main]}}\njobs:\n"
    + CHANGES % ("pins", "pins", "pins", "'resources.version'")
    + "  x:\n    needs: [changes]\n    if: needs.changes.outputs.pins-x == 'true'\n    steps: []\n",
    "marola-corpus/.github/workflows/pr.yml": "on: {pull_request: {}}\njobs:\n"
    + CHANGES % ("docker", "docker", "docker", "'corpus.version'")
    + "  b:\n    needs: changes\n    if: needs.changes.outputs.docker == 'true'\n    steps: []\n",
}

API_ROW = "| `api-docs` branch | marola-app `api-docs.yml` (`api-docs.yml@v0.4.1`, on a push to `main`), marola-corpus `api-docs.yml` (`api-docs.yml@v9.9.9` (resolved at v0.6.0), on a push to `main`) | — | marola `scripts/fetch-api-docs.sh` |"

# Each case is rows the fixture's block must hold; a "!" line is text it must not hold.
CASES = {
    "dispatch_types_listened": "| `site-data-updated` | marola-app `ci.yml` (on a push to `main`) | marola-site `site.yml` |\n| `submodule-docs-updated` | marola-site `notify-umbrella.yml` (`notify-umbrella.yml@v0.3.1`, on a push to `main` touching `README.md`, `docs/**`) | marola `pointer-sync.yml` |",
    "dispatch_send_step": "| `site-data-updated` | marola-app `ci.yml` (on a push to `main`) | marola-site `site.yml` |",
    "reusable_workflow_ref_and_event_type": "| `submodule-updated` | marola-app `notify-umbrella.yml` (`notify-umbrella.yml@v0.6.0`, on a push to `main`) | marola `pointer-sync.yml` |\n| a marola-devkit tag | marola-devkit | marola-app `flake.lock` v0.4.1 | `api-docs.yml` v0.4.1 (marola-app), v9.9.9 (marola-corpus); `gemini-review.yml` v0.5.0 (marola-site); `notify-umbrella.yml` v0.3.1 (marola-site), v0.6.0 (marola-app); `ping.yml` v0.3.1 (marola-ml) |",
    "reusable_workflow_resolved_at_caller_ref": "| `submodule-docs-updated` | marola-site `notify-umbrella.yml` (`notify-umbrella.yml@v0.3.1`, on a push to `main` touching `README.md`, `docs/**`) | marola `pointer-sync.yml` |\n| `submodule-updated` | marola-app `notify-umbrella.yml` (`notify-umbrella.yml@v0.6.0`, on a push to `main`) | marola `pointer-sync.yml` |\n"
    + API_ROW,
    "release_upload_assets": "| `marola-corpus-<tag>.tar.gz` release asset | marola-corpus `release.yml` (on a `v*` tag) | marola-ml `corpus.version` | marola-ml `scripts/corpus-fetch.sh` |",
    "release_create_without_assets": "!marola `release.yml`",
    "only_devkit_calls_in_tag_row": "!reusable.yml",
    "unresolved_event_type_not_sent": "!inputs.ev",
    "image_publish": "| `ghcr.io/marola-dev/marola-app` image | marola-app `docker.yml` (on a push to `main` touching `corpus.version`) | marola-site `marola-image` | marola-site `scripts/board-schema.sh` |",
    "deploy_pages_site": "| marola `docs.yml` | docs.marola.dev |\n| marola-site `site.yml` | marola.dev |",
    "pin_file_and_reader": "| `marola-corpus-<tag>.tar.gz` release asset | marola-corpus `release.yml` (on a `v*` tag) | marola-ml `corpus.version` | marola-ml `scripts/corpus-fetch.sh` |\n| marola-app `corpus.version` | marola-app `docker.yml` |\n| marola-site `marola-image` | marola-site `site.yml` |",
    "branch_artifact_api_docs": API_ROW,
    "branch_artifact_site_data": "| `site-data` branch of marola-site | marola-app `ci.yml` (on a push to `main`) | — | marola-site `site.yml` |\n!pushes to marola-site",
    "compose_image_reader": "| `ghcr.io/marola-dev/marola-ml` image | marola-ml `docker-local.yml` (on a push to `main`) | — | marola-app `docker-compose.yml` |",
    "cross_repo_pr": "| PRs into marola-app: `recommendation_prompt.json`, `review_prompt.json` | marola-ml `compile-prompt.yml` (by hand) | — | marola-app |\n!pushes to marola-app",
    "cross_repo_push": "| pushes to marola-oods | marola-app `ingest.yml` (on a schedule) | — | marola-oods |",
    "cross_repo_pr_each_listed": "| PRs into each repo in `.github/consumers.txt` | marola-devkit `bump-consumers.yml` (by hand) | — | marola, marola-app |",
    "own_repo_pr_dropped": "!self-pr.yml",
    "image_reader_forms": "| `ghcr.io/marola-dev/marola-corpus` image | marola-corpus `docker.yml` (on a push to `main`) | — | marola-site `docker-compose.yml`, marola-ml `Dockerfile`, marola-ml `docker-compose.yml` |",
    "commented_pin_not_a_reader": "!marola-ml `justfile`",
    "paths_filter_gating_a_job_is_a_pin_trigger": "| marola-ml `corpus.version` | marola-ml `ci.yml` |",
    "paths_filter_gating_no_job_adds_no_row": "!marola-corpus `resources.version`",
    "paths_filter_without_push_adds_no_row": "!marola-corpus `corpus.version`",
}


def _devkit_tags(dk: Path) -> None:
    def g(*args: str) -> None:
        cfg = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        cfg += ["-c", "tag.gpgsign=false"]
        subprocess.run(["git", "-C", str(dk), *cfg, *args], check=True, capture_output=True)

    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "v0.3.1", "--no-verify")
    g("tag", "v0.3.1")
    g("tag", "v0.4.1")
    nu = dk / ".github/workflows/notify-umbrella.yml"
    nu.write_text(
        nu.read_text().replace("default: submodule-docs-updated", "default: submodule-updated")
    )
    g("commit", "-qam", "v0.6.0", "--no-verify")
    g("tag", "v0.6.0")


def self_test() -> int:
    global fetch
    served: dict[str, str | None] = {}

    def fake(url: str) -> str | None:
        if url not in served:
            raise OSError("offline")
        return served[url]

    fetch = fake
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["XDG_CACHE_HOME"] = str(root / ".cache")
        for rel, text in FIXTURE.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        for path in re.findall(r"path = (\S+)", FIXTURE[".gitmodules"]):
            (root / path / ".git").write_text("gitdir: unused\n", encoding="utf-8")
        _devkit_tags(root / ".devkit")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            w = scan(root)
        block = render(w)
        unreadable = check(w, block, None, "")
        w.unread.clear()  # the check cases below count the other problems only
        as_json = json.loads(to_json(w))
        contract_files = {
            "marola-app/cli/src/main/resources/board.schema.json": '{"type": "object"}\n',
            "marola-site/site/board.schema.json": '{"type": "object"}\n',
            "marola-site/site/areas.json": '["rj"]\n',
            "marola-app/cli/src/test/resources/areas.json": '["rj"]\n',
            "marola-ml/a.json": '{"a": 1}\n',
            "marola-corpus/a.json": '{"a": 2}\n',
            "marola-ml/conf/drift.yaml": "a: 1\n",
            "marola-corpus/drift.yaml": "a: 2\n",
            "marola-ml/.github/x.json": "{}\n",
            "marola-corpus/.github/x.json": "{}\n",
            "wiring.allow": "contract marola-ml/conf/drift.yaml marola-corpus/drift.yaml: the corpus adds a key\n"
            "contract marola-ml/gone.yaml marola-corpus/drift.yaml: stale\n",
        }
        for rel, text in contract_files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            cw = scan(root)
        contract_rows = render(cw).splitlines()
        contract_json = json.loads(to_json(cw)).get("contracts")
        contract_problems = check(cw, render(cw), None, read(root / "wiring.allow"))
        for rel in contract_files:
            (root / rel).unlink()
        shutil.rmtree(root / ".devkit" / ".git")  # a Nix store path
        raw = f"https://raw.githubusercontent.com/{ORG}/%s/%s/.github/workflows/%s"
        served[raw % ("marola-devkit", "v0.3.1", "notify-umbrella.yml")] = FIXTURE[
            ".devkit/.github/workflows/notify-umbrella.yml"
        ]
        served[raw % ("marola-devkit", "v9.9.9", "api-docs.yml")] = None
        served[raw % ("marola-app", "main", "reusable.yml")] = "on: {workflow_call: {}}\n"
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            fetched = render(scan(root)).splitlines()
        cache = root / ".cache" / "marola-wiring"
        tagged = (cache / "marola-devkit" / "v0.3.1" / "notify-umbrella.yml").is_file()
        branch = (cache / "marola-app").exists()
        (root / "marola-x").mkdir()
        (root / ".gitmodules").write_text("[submodule]\n\tpath = marola-x\n", encoding="utf-8")
        try:
            scan(root)
            uninit = ""
        except SystemExit as e:
            uninit = str(e)
        (root / "REPOS.md").write_text(
            f"# Repos\n\n{START}\nstale\n{END}\n\nAfter.\n", encoding="utf-8"
        )
        write_block(root / "REPOS.md", block)
        repos_md = (root / "REPOS.md").read_text(encoding="utf-8")
        (root / "STALE.md").write_text(f"{START}\n\n{block}| x |\n{END}\n", encoding="utf-8")
        fresh = check(w, block, root / "REPOS.md", "")
        stale = check(w, block, root / "STALE.md", "")
        ml = "ghcr.io/marola-dev/marola-ml image"
        w.artifacts["`ghcr.io/marola-dev/marola-ml` image"]["read"].clear()
        unread = check(w, block, None, "")
        allowed = check(w, block, None, f"# kept\n\n{ml}: kept for local runs\n")
        no_reason = check(w, block, None, f"{ml}:  \n")
        no_artifact = check(w, block, None, f"{ml}: kept\ngone image: retired\n")
        w.dispatch["site-data-updated"]["sent"].clear()
        w.dispatch["submodule-updated"]["listen"].clear()
        orphans = check(w, block, None, f"{ml}: kept\n")
        every = check(w, block, root / "STALE.md", "")
    rows = block.splitlines()
    cases = [
        (name, all(x[1:] not in block if x[0] == "!" else x in rows for x in want.split("\n")))
        for name, want in CASES.items()
    ]
    shell = 'gh release upload --clobber "$TAG" "$f"; echo done\ngh release create "$TAG" --discussion-category General --notes-start-tag v1 \\\n  dist/a.tgz>out.log\n'
    array = load(
        "on: {workflow_call: {}}\njobs:\n  d:\n    steps:\n      - run: |\n"
        '          events=(submodule-updated)\n          [ -z "$DOCS" ] || events+=(submodule-docs-updated)\n'
        '          for e in "${events[@]}"; do curl https://api.github.com/repos/$U/dispatches -d x; done\n',
        "array fixture",
    )
    sent = sorted(
        v for k, v, _ in effects(array, "r", {}, lambda *a: (None, ""), root) if k == "send"
    )
    cases.append(("dispatch_array_send", sent == ["submodule-docs-updated", "submodule-updated"]))
    cases.append(
        (
            "contract_copy_linked",
            contract_json
            == {
                "marola-app/cli/src/main/resources/board.schema.json": {
                    "copies": ["marola-site/site/board.schema.json"],
                    "note": "identical",
                },
                "marola-ml/conf/drift.yaml": {
                    "copies": ["marola-corpus/drift.yaml"],
                    "note": "the corpus adds a key",
                },
                "marola-site/site/areas.json": {
                    "copies": ["marola-app/cli/src/test/resources/areas.json"],
                    "note": "identical",
                },
            }
            and "| marola-app `cli/src/main/resources/board.schema.json` | marola-site `site/board.schema.json` | identical |"
            in contract_rows,
        )
    )
    cases.append(
        (
            "contract_line_checked_not_an_artifact",
            [p for p in contract_problems if p.startswith("wiring.allow")]
            == ["wiring.allow: contract `marola-ml/gone.yaml` names no file"],
        )
    )
    wf_dispatch = load(
        "on: {workflow_dispatch: {}}\njobs:\n  d:\n    steps:\n      - run: |\n"
        "          gh api repos/o/r/actions/workflows/x.yml/dispatches -f ref=main\n"
        '          curl -X POST "https://api.github.com/repos/o/r/actions/workflows/1/dispatches"\n',
        "workflow_dispatch fixture",
    )
    kinds = {k for k, _, _ in effects(wf_dispatch, "r", {}, lambda *a: (None, ""), root)}
    cases.append(("workflow_dispatch_api_not_unread", "unread-dispatch" not in kinds))
    cases.append(
        (
            "json_carries_the_block",
            sorted(as_json["artifacts"]) == sorted(w.artifacts)
            and sorted(as_json["dispatch"]) == sorted(w.dispatch)
            and all(isinstance(a["repos"], list) for a in as_json["artifacts"].values()),
        )
    )
    cases.append(("release_files_shell_forms", release_files(shell) == ["$f", "dist/a.tgz"]))
    cases.append(("missing_callee_warns", "gemini-review.yml@v0.5.0 not found" in err.getvalue()))
    cases.append(("uncloned_callee_warns", "marola-infra/x.yml@v1 not found" in err.getvalue()))
    nu = "| `submodule-docs-updated` | marola-site `notify-umbrella.yml` (`notify-umbrella.yml@v0.3.1`, on a push to `main` touching `README.md`, `docs/**`) | marola `pointer-sync.yml` |"
    cases.append(("callee_fetched_at_ref_without_git", nu in fetched))
    cases.append(
        (
            "callee_404_not_resolved_at_tree",
            "api-docs.yml@v9.9.9 not found" in err2.getvalue()
            and not any("v9.9.9` (resolved" in r for r in fetched),
        )
    )
    cases.append(("tag_fetch_cached_branch_not", tagged and not branch))
    cases.append(("uninitialised_submodule_fails", "marola-x is not checked out" in uninit))
    cases.append(("block_between_markers", f"{START}\n\n{block}\n{END}\n\nAfter." in repos_md))
    cases.append(("check_passes_fresh_block_and_reasoned_allow", fresh == allowed == []))
    cases.append(("check_fails_on_stale_block", len(stale) == 1 and "just wiring" in stale[0]))
    cases.append(
        (
            "check_fails_on_orphan_dispatch",
            orphans
            == [
                "dispatch `site-data-updated` is listened for but never sent",
                "dispatch `submodule-updated` is sent but never listened for",
            ],
        )
    )
    cases.append(
        (
            "check_fails_on_unread_artifact",
            len(unread) == 1 and unread[0].startswith(f"`{ml}` has no reader"),
        )
    )
    cases.append(("allow_entry_needs_reason", any("needs a reason" in p for p in no_reason)))
    cases.append(
        (
            "allow_entry_names_an_artifact",
            no_artifact == ["wiring.allow: `gone image` names no artifact"],
        )
    )
    cases.append(("check_reports_every_problem", len(every) == 4))
    cases.append(
        (
            "check_fails_on_unreadable_dispatch_once_per_caller",
            unreadable
            == [
                f"marola-ml `ping.yml`: {UNREAD}",
                f"marola-site `odd.yml`: {UNREAD}",
            ],
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        wf = Path(tmp) / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "l.yml").write_text(
            "on:\n  repository_dispatch:\n    types: [x]\njobs: {}\n", encoding="utf-8"
        )
        lonely = lint_devkit(Path(tmp))
        n = "on: {workflow_call: {}}\njobs:\n  n:\n    steps:\n      - env: {EVENT_TYPE: x}\n        run: curl https://api.github.com/repos/x/dispatches -d y\n"
        (wf / "n.yml").write_text(n, encoding="utf-8")
        caller = "on: {workflow_dispatch: {}}\njobs:\n  c:\n    uses: %s\n"
        (wf / "c.yml").write_text(caller % "./.github/workflows/n.yml", encoding="utf-8")
        local = lint_devkit(Path(tmp))
        tagged = f"{ORG}/marola-devkit/.github/workflows/n.yml@v9.9.9"
        (wf / "c.yml").write_text(caller % tagged, encoding="utf-8")
        self_path = lint_devkit(Path(tmp))
        (wf / "n.yml").write_text(
            n.replace("{EVENT_TYPE: x}", "{TOKEN: t}").replace("-d y", '-d \'{"event_type":"x"}\''),
            encoding="utf-8",
        )
        json_body = lint_devkit(Path(tmp))
        (wf / "c.yml").write_text(caller % "./.github/workflows/c.yml", encoding="utf-8")
        loop = lint_devkit(Path(tmp))
    cases.append(("lint_devkit_fails_on_listener_without_sender", lonely == [ORPHAN % "x"]))
    cases.append(("lint_devkit_local_call_sends", local == []))
    cases.append(
        (
            "lint_devkit_call_cycle",
            loop[0] == "marola-devkit `c.yml`: a reusable-workflow call cycle",
        )
    )
    cases.append(("lint_devkit_tagged_self_path_resolves_locally", self_path == []))
    cases.append(
        (
            "lint_devkit_fails_on_unreadable_dispatch",
            json_body == [f"marola-devkit `c.yml`: {UNREAD}", ORPHAN % "x"],
        )
    )
    fails = [n for n, ok in cases if not ok]
    for name, ok in cases:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if fails:
        print(block, file=sys.stderr)
        print(f"wiring self-test: {len(fails)} failure(s)", file=sys.stderr)
        return 1
    print("wiring self-test: ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "file", nargs="?", type=Path, help="rewrite the block between this file's markers"
    )
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--devkit", type=Path, help="default: <root>/.devkit, else this script's repo")
    ap.add_argument("--name", default="marola", help="the umbrella's repo name")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument(
        "--lint-devkit",
        nargs="?",
        const=Path(__file__).resolve().parents[1],
        type=Path,
        metavar="DIR",
    )
    mode.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.lint_devkit:
        problems = lint_devkit(args.lint_devkit)
        for p in problems:
            print(f"wiring: {p}", file=sys.stderr)
        return 1 if problems else 0
    w = scan(args.root, args.name, args.devkit)
    if args.json:
        print(to_json(w))
        return 0
    block = render(w)
    if args.check:
        problems = check(w, block, args.file, read(args.root / "wiring.allow"))
        for p in problems:
            print(f"wiring: {p}", file=sys.stderr)
        return 1 if problems else 0
    if args.file:
        write_block(args.file, block)
    else:
        sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
