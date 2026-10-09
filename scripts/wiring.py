#!/usr/bin/env python3
"""The org's cross-repo wiring, parsed from every repo's workflows, pins and scripts (MIP-0076 §5.1).

Run in an umbrella checkout with its submodules; the devkit's tree is `.devkit`, else this script's.

wiring [--root DIR] [--devkit DIR] [--name NAME] [FILE]
    print the block, or rewrite it between FILE's wiring markers
wiring --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shlex
import sys
import tempfile
from pathlib import Path

import yaml

ORG = "marola-dev"
PINS = ("marola-image", "corpus.version", "resources.version")
START, END = "<!-- wiring:start -->", "<!-- wiring:end -->"
USES = re.compile(rf"^{ORG}/([\w.-]+)/\.github/workflows/([\w.-]+)@([\w.-]+)$")
IMAGE = re.compile(rf"ghcr\.io/{ORG}/[\w.-]+")
RELEASE = re.compile(r"gh release (?:upload|create)\b([^\n]*)")
# gh release flags that take a value; the first other argument is the tag, the rest are files.
VALUED = {"--repo", "-R", "--title", "-t", "--notes", "-n", "--notes-file", "-F", "--target"}
VALUED |= {"--discussion-category", "--notes-start-tag"}
ASSIGN = re.compile(r"""^\s*(\w+)=["']?([^"'\s]+)""", re.M)
SHVAR = re.compile(r"\$\{?(\w+)\}?")
EXPR = re.compile(r"\$\{\{\s*(env|inputs|github)\.([\w-]+)\s*\}\}")


class Wiring:
    def __init__(self) -> None:
        self.artifacts: dict[str, dict] = {}
        self.dispatch: dict[str, dict[str, list[str]]] = {}
        self.bumps: dict[str, list[str]] = {}
        self.deploys: list[tuple[str, str]] = []
        self.devkit: dict[str, list[str]] = {}
        self.calls: dict[str, dict[str, list[str]]] = {}

    def art(self, key: str) -> dict:
        return self.artifacts.setdefault(key, {"pub": [], "pin": [], "read": []})


def add(xs: list[str], x: str) -> None:
    if x not in xs:
        xs.append(x)


def sub(s: str, env: dict, inputs: dict) -> str:
    def expr(m: re.Match) -> str:
        ctx, name = m.groups()
        if ctx == "github":
            return ORG if name == "repository_owner" else m[0]
        return str((env if ctx == "env" else inputs).get(name, m[0]))

    s = EXPR.sub(expr, str(s))
    return SHVAR.sub(lambda m: str(env.get(m[1], m[0])), s)


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


def warn(msg: str) -> None:
    print(f"wiring: warning: {msg}", file=sys.stderr)


def load(text: str, where: object) -> dict:
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"wiring: {where}: {e}") from None


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


def effects(doc: dict, repo: str, inputs: dict, callee) -> list[tuple]:
    """(kind, value, via) for what a workflow sends, publishes or deploys."""
    out: list[tuple] = []
    text = yaml.safe_dump(doc)
    for job in (doc.get("jobs") or {}).values():
        env = {**(doc.get("env") or {}), **(job.get("env") or {})}
        if m := USES.match(str(job.get("uses", ""))):
            target, note = callee(m[1], m[2], m[3])
            if target:
                defaults = triggers(target).get("workflow_call", {}).get("inputs") or {}
                given = {k: v.get("default", "") for k, v in defaults.items()}
                given |= {k: sub(v, env, inputs) for k, v in (job.get("with") or {}).items()}
                via = f"`{m[2]}@{m[3]}`{note}"
                out += [(k, v, via) for k, v, _ in effects(target, repo, given, callee)]
            if m[1] == "marola-devkit":
                out.append(("ref", (m[2], m[3]), ""))
            continue
        for step in job.get("steps") or []:
            senv = {**env, **(step.get("env") or {})}
            uses = str(step.get("uses", ""))
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
            sent = re.findall(r"event_type=([^\s\"']*)", sub(run, senv, inputs))
            out += [("send", t, "") for t in sent if t and "$" not in t]
            if "/dispatches" in run:
                evs = [sub(v, {}, inputs) for k, v in senv.items() if "EVENT" in k]
                out += [("send", t, "") for t in evs if "$" not in t]
            for asset in release_files(run):
                name = sub(asset, assigned, {}).rsplit("/", 1)[-1]
                out.append(("asset", SHVAR.sub("<tag>", name), ""))
    return out


def scan(root: Path, name: str = "marola", devkit: Path | None = None) -> Wiring:
    w, rs = Wiring(), repos(root, name, devkit)
    docs: dict[tuple[str, str], dict] = {}
    for repo, d in rs:
        for f in sorted((d / ".github" / "workflows").glob("*.y*ml")):
            docs[(repo, f.name)] = load(read(f), f)

    def callee(repo: str, file: str, ref: str) -> tuple[dict | None, str]:
        if (repo, file) not in docs:
            warn(f"{repo}/{file}@{ref} not found; its effects are not shown")
        return docs.get((repo, file)), ""

    for (repo, file), doc in docs.items():
        on = triggers(doc)
        if set(on) == {"workflow_call"}:
            continue
        label = f"{repo} `{file}`"
        for t in on.get("repository_dispatch", {}).get("types") or []:
            add(w.dispatch.setdefault(t, {"sent": [], "listen": []})["listen"], label)
        for pin in set(PINS + ("flake.lock",)) & set(on.get("push", {}).get("paths") or []):
            add(w.bumps.setdefault(f"{repo} `{pin}`", []), label)
        repo_dir = dict(rs)[repo]
        for kind, v, via in effects(doc, repo, {}, callee):
            parts = [p for p in (via, when(on)) if p]
            pub = label + (f" ({', '.join(parts)})" if parts else "")
            if kind == "send":
                add(w.dispatch.setdefault(v, {"sent": [], "listen": []})["sent"], pub)
            elif kind == "image":
                add(w.art(f"`{v}` image")["pub"], pub)
            elif kind == "asset":
                add(w.art(f"`{v}` release asset")["pub"], pub)
            elif kind == "deploy":
                w.deploys.append((label, site_of(repo_dir, v)))
            elif kind == "ref":
                add(w.calls.setdefault(v[0], {}).setdefault(v[1], []), repo)
    for repo, d in rs:
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
    return w


def render(w: Wiring) -> str:
    def cell(xs) -> str:
        return ", ".join(xs) or "—"

    out = ["| Artifact | Published by | Pinned in | Read by |", "|---|---|---|---|"]
    kinds = (" image", "release asset")
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
    return "\n".join(out) + "\n"


def write_block(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition(START)
    _, sep2, tail = rest.partition(END)
    if not (sep and sep2):
        raise SystemExit(f"wiring: {path} has no {START} … {END} markers")
    path.write_text(f"{head}{START}\n\n{block}\n{END}{tail}", encoding="utf-8")


CO = "      - uses: actions/checkout@v7\n        with: {repository: %s, path: %s%s}\n"
FIXTURE = {
    ".gitmodules": "".join(
        f'[submodule "{r}"]\n\tpath = {r}\n'
        for r in ("marola-app", "marola-site", "marola-corpus", "marola-ml")
    ),
    "mkdocs/mkdocs.yml": "site_name: x\nsite_url: https://docs.marola.dev/\n",
    ".github/workflows/pointer-sync.yml": "on:\n  repository_dispatch:\n    types: [submodule-updated, submodule-docs-updated]\njobs: {}\n",
    ".github/workflows/release.yml": 'on: {push: {tags: [v*]}}\njobs:\n  r:\n    steps:\n      - run: gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --verify-tag --title "x $TAG"\n',
    ".github/workflows/docs.yml": "on: {push: {paths: [flake.lock]}}\njobs:\n  d:\n    steps:\n      - uses: actions/deploy-pages@v5\n",
    ".devkit/.github/workflows/notify-umbrella.yml": "on:\n  workflow_call:\n    inputs:\n      event-type: {type: string, default: submodule-docs-updated}\njobs:\n  dispatch:\n    steps:\n      - env: {EVENT_TYPE: '${{ inputs.event-type }}'}\n        run: curl https://api.github.com/repos/$UMBRELLA/dispatches -d x\n",
    ".devkit/.github/workflows/api-docs.yml": "on: {workflow_call: {inputs: {devkit-ref: {type: string}}}}\njobs:\n  publish:\n    steps:\n      - run: bash .devkit-checkout/scripts/api-docs-push.sh out url sha\n",
    "marola-site/.github/workflows/notify-umbrella.yml": "on: {push: {branches: [main], paths: [README.md, 'docs/**']}}\njobs:\n  n:\n    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.3.1\n",
    "marola-site/.github/workflows/site.yml": 'on:\n  push: {paths: [marola-image]}\n  repository_dispatch: {types: [site-data-updated]}\njobs:\n  b:\n    steps:\n      - run: git fetch --depth=1 origin site-data\n      - run: echo "marola.dev" > site/dist/CNAME\n      - uses: actions/deploy-pages@v5\n',
    "marola-corpus/.github/workflows/api-docs.yml": "on: {push: {branches: [main]}}\njobs:\n  a:\n    uses: marola-dev/marola-devkit/.github/workflows/api-docs.yml@v9.9.9\n",
    "marola-site/.github/workflows/odd.yml": "on: {workflow_dispatch: {}}\njobs:\n  r:\n    uses: marola-dev/marola-app/.github/workflows/reusable.yml@main\n  d:\n    env: {EVENT_TYPE: '${{ inputs.ev }}'}\n    steps:\n      - run: curl https://api.github.com/repos/x/dispatches -d y\n",
    "marola-site/.github/workflows/gemini.yml": "on: {pull_request: {}}\njobs:\n  g:\n    uses: marola-dev/marola-devkit/.github/workflows/gemini-review.yml@v0.5.0\n",
    "marola-corpus/.github/workflows/release.yml": 'on: {push: {tags: [\'v*\']}}\njobs:\n  t:\n    steps:\n      - run: |\n          file=".tmp/marola-corpus-$TAG.tar.gz"\n          gh release upload "$TAG" "$file"\n',
    "marola-app/.github/workflows/docker.yml": "on: {push: {branches: [main], paths: [corpus.version]}}\nenv: {IMAGE: 'ghcr.io/${{ github.repository_owner }}/marola-app'}\njobs:\n  jvm:\n    steps:\n      - uses: docker/build-push-action@v7\n",
    "marola-app/.github/workflows/api-docs.yml": "on: {push: {branches: [main]}}\njobs:\n  a:\n    uses: marola-dev/marola-devkit/.github/workflows/api-docs.yml@v0.4.1\n    with: {devkit-ref: v0.4.1}\n",
    "marola-app/.github/workflows/notify-umbrella.yml": "on: {push: {branches: [main]}}\njobs:\n  n:\n    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.6.0\n    with: {event-type: submodule-updated}\n",
    "marola-app/.github/workflows/ci.yml": "on: {push: {branches: [main]}}\njobs:\n  c:\n    steps:\n"
    + CO % ("marola-dev/marola-site", "site-data", ", ref: site-data")
    + "      - run: |\n          git -C site-data commit -m x\n          scripts/site-data-push.sh site-data\n      - run: gh api repos/marola-dev/marola-site/dispatches -f event_type=site-data-updated\n",
    "marola-app/flake.lock": json.dumps(
        {"nodes": {"marola-devkit": {"original": {"repo": "marola-devkit", "ref": "v0.4.1"}}}}
    ),
    "marola-ml/.github/workflows/docker-local.yml": "on: {push: {branches: [main]}}\nenv: {IMAGE: ghcr.io/marola-dev/marola-ml}\njobs:\n  b:\n    steps:\n      - uses: docker/build-push-action@v7\n",
}

# Each case is rows the fixture's block must hold; a "!" line is text it must not hold.
CASES = {
    "dispatch_types_listened": "| `site-data-updated` | marola-app `ci.yml` (on a push to `main`) | marola-site `site.yml` |\n| `submodule-docs-updated` | marola-site `notify-umbrella.yml` (`notify-umbrella.yml@v0.3.1`, on a push to `main` touching `README.md`, `docs/**`) | marola `pointer-sync.yml` |",
    "dispatch_send_step": "| `site-data-updated` | marola-app `ci.yml` (on a push to `main`) | marola-site `site.yml` |",
    "reusable_workflow_ref_and_event_type": "| `submodule-updated` | marola-app `notify-umbrella.yml` (`notify-umbrella.yml@v0.6.0`, on a push to `main`) | marola `pointer-sync.yml` |\n| a marola-devkit tag | marola-devkit | marola-app `flake.lock` v0.4.1 | `api-docs.yml` v0.4.1 (marola-app), v9.9.9 (marola-corpus); `gemini-review.yml` v0.5.0 (marola-site); `notify-umbrella.yml` v0.3.1 (marola-site), v0.6.0 (marola-app) |",
    "release_upload_assets": "| `marola-corpus-<tag>.tar.gz` release asset | marola-corpus `release.yml` (on a `v*` tag) | — | — |",
    "release_create_without_assets": "!marola `release.yml`",
    "only_devkit_calls_in_tag_row": "!reusable.yml",
    "unresolved_event_type_not_sent": "!inputs.ev",
    "image_publish": "| `ghcr.io/marola-dev/marola-app` image | marola-app `docker.yml` (on a push to `main` touching `corpus.version`) | — | — |",
    "pin_bump_on_push_paths": "| marola-app `corpus.version` | marola-app `docker.yml` |\n| marola-site `marola-image` | marola-site `site.yml` |",
    "deploy_pages_site": "| marola `docs.yml` | docs.marola.dev |\n| marola-site `site.yml` | marola.dev |",
}


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in FIXTURE.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        for path in re.findall(r"path = (\S+)", FIXTURE[".gitmodules"]):
            (root / path / ".git").write_text("gitdir: unused\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            w = scan(root)
        block = render(w)
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
    rows = block.splitlines()
    cases = [
        (name, all(x[1:] not in block if x[0] == "!" else x in rows for x in want.split("\n")))
        for name, want in CASES.items()
    ]
    shell = 'gh release upload --clobber "$TAG" "$f"; echo done\ngh release create "$TAG" --discussion-category General --notes-start-tag v1 \\\n  dist/a.tgz>out.log\n'
    cases.append(("release_files_shell_forms", release_files(shell) == ["$f", "dist/a.tgz"]))
    cases.append(("missing_callee_warns", "gemini-review.yml@v0.5.0 not found" in err.getvalue()))
    cases.append(("uninitialised_submodule_fails", "marola-x is not checked out" in uninit))
    cases.append(("block_between_markers", f"{START}\n\n{block}\n{END}\n\nAfter." in repos_md))
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
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    block = render(scan(args.root, args.name, args.devkit))
    if args.file:
        write_block(args.file, block)
    else:
        sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
