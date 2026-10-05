#!/usr/bin/env python3
"""The org's cross-repo wiring, parsed from every repo's workflows, pins and scripts (MIP-0076 §5.1).

Run in an umbrella checkout with its submodules; the devkit's tree is `.devkit`, else this script's.

wiring [--root DIR] [FILE]   print the block, or rewrite it between FILE's wiring markers
wiring --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ORG = "marola-dev"
SELF = Path(__file__)  # its fixture names every idiom, so it is never a reader
PINS = ("marola-image", "corpus.version", "resources.version")
START, END = "<!-- wiring:start -->", "<!-- wiring:end -->"
READERS = ("scripts/**/*", "justfile", "Dockerfile*", "docker-compose*.yml", "build.sbt")
USES = re.compile(rf"^{ORG}/([\w.-]+)/\.github/workflows/([\w.-]+)@([\w.-]+)$")
IMAGE = re.compile(rf"ghcr\.io/{ORG}/[\w.-]+")
RELEASE = re.compile(r"gh release (?:upload|create)\s+\S+\s+(\S+)")
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
            return ORG if name == "repository_owner" else m[0]
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


def repos(root: Path) -> list[tuple[str, Path]]:
    url = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    out = [(re.sub(r"\.git$", "", url.rsplit("/", 1)[-1]) or "marola", root)]
    for path in re.findall(r"^\s*path\s*=\s*(\S+)", read(root / ".gitmodules"), re.M):
        out.append((Path(path).name, root / path))
    devkit = root / ".devkit"
    out.append(
        ("marola-devkit", devkit if devkit.is_dir() else Path(__file__).resolve().parents[1])
    )
    return [(n, p) for n, p in out if p.is_dir()]


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
        touching = " touching " + ", ".join(f"`{p}`" for p in paths) if 0 < len(paths) <= 3 else ""
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


def effects(doc: dict, repo: str, inputs: dict, workflows: dict) -> list[tuple]:
    """(kind, value, via) for what a workflow sends, publishes, deploys or writes elsewhere."""
    out: list[tuple] = []
    text = yaml.safe_dump(doc)
    for job in (doc.get("jobs") or {}).values():
        env = {**(doc.get("env") or {}), **(job.get("env") or {})}
        if m := USES.match(str(job.get("uses", ""))):
            target = workflows.get((m[1], m[2]))
            if target:
                defaults = triggers(target).get("workflow_call", {}).get("inputs") or {}
                given = {k: v.get("default", "") for k, v in defaults.items()}
                given |= {k: sub(v, env, inputs) for k, v in (job.get("with") or {}).items()}
                via = f"`{m[2]}@{m[3]}`"
                out += [(k, v, via) for k, v, _ in effects(target, repo, given, workflows)]
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
            out += [("send", t, "") for t in re.findall(r"event_type=([\w-]+)", run)]
            if "/dispatches" in run:
                out += [("send", sub(v, {}, inputs), "") for k, v in senv.items() if "EVENT" in k]
            for asset in RELEASE.findall(run):
                name = sub(asset.strip("\"'"), assigned, {}).rsplit("/", 1)[-1]
                out.append(("asset", SHVAR.sub("<tag>", name), ""))
            if branches := re.findall(r"([\w-]+)-push\.sh\b", run):
                for b in branches:
                    owner = next((r for r, ref in checkouts.values() if ref == b), repo)
                    out.append(("branch", (b, owner), ""))
                continue
            if m := re.search(r"gh pr create\b.*?--repo\s+(\S+)", run, re.S):
                files = sorted(
                    {Path(f).name for f in " ".join(re.findall(r"git add\s+(.+)", run)).split()}
                )
                out.append(
                    ("pr", (sub(m[1].strip("\"'"), senv, inputs).rsplit("/", 1)[-1], files), "")
                )
            elif m := re.search(r"(?:git -C (\S+) |cd (\S+)[^\n]*\n(?:.*\n)*?\s*git )push\b", run):
                target = checkouts.get((m[1] or m[2]).strip("\"'"))
                if target:
                    out.append(("push", target[0], ""))
    return [(k, v, via) for k, v, via in out if k not in ("pr", "push") or v[0] != repo]


def scan(root: Path) -> Wiring:
    w, rs = Wiring(), repos(root)
    docs: dict[tuple[str, str], dict] = {}
    for name, d in rs:
        for f in sorted((d / ".github" / "workflows").glob("*.y*ml")):
            docs[(name, f.name)] = yaml.safe_load(read(f)) or {}
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
        for kind, v, via in effects(doc, repo, {}, docs):
            parts = [p for p in (via, when(on)) if p]
            pub = label + (f" ({', '.join(parts)})" if parts else "")
            if kind == "send":
                add(w.dispatch.setdefault(v, {"sent": [], "listen": []})["sent"], pub)
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
                add(a["pub"], pub), add(a["read"], v[0])
            elif kind == "push":
                a = w.art(f"pushes to {v}", v)
                add(a["pub"], pub), add(a["read"], v)
            elif kind == "deploy":
                w.deploys.append((label, site_of(repo_dir, v)))
            elif kind == "ref":
                add(w.calls.setdefault(v[0], {}).setdefault(v[1], []), repo)
    for repo, d in rs:
        files = sorted(
            {f for g in READERS for f in d.glob(g) if f.is_file() and f.name != SELF.name}
        )
        texts = {f.relative_to(d).as_posix(): lines(read(f)) for f in files}
        for f in sorted((d / ".github" / "workflows").glob("*.y*ml")):
            texts[f".github/workflows/{f.name}"] = lines(read(f))
        for rel, text in texts.items():
            for img in re.findall(r"(?:image:|FROM)\s+(" + IMAGE.pattern + ")", text):
                if (key := f"`{img}` image") in w.artifacts:
                    add(w.artifacts[key]["read"], f"{repo} `{Path(rel).name}`")
            for key, a in w.artifacts.items():
                b = a["match"]
                if " branch" not in key or rel.endswith(f"{b}-push.sh"):
                    continue
                names = {b} | {v for v, x in ASSIGN.findall(text) if x == b}
                if any(
                    "fetch" in ln
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
    return w


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
    "scripts/fetch-api-docs.sh": 'API_DOCS_BRANCH="api-docs"\ngit -C "$tmp" fetch -q "$url" "$API_DOCS_BRANCH"\n',
    ".github/workflows/pointer-sync.yml": "on:\n  repository_dispatch:\n    types: [submodule-updated, submodule-docs-updated]\njobs: {}\n",
    ".github/workflows/docs.yml": "on: {push: {paths: [flake.lock]}}\njobs:\n  d:\n    steps:\n      - uses: actions/deploy-pages@v5\n",
    ".devkit/.github/workflows/notify-umbrella.yml": "on:\n  workflow_call:\n    inputs:\n      event-type: {type: string, default: submodule-docs-updated}\njobs:\n  dispatch:\n    steps:\n      - env: {EVENT_TYPE: '${{ inputs.event-type }}'}\n        run: curl https://api.github.com/repos/$UMBRELLA/dispatches -d x\n",
    ".devkit/.github/workflows/api-docs.yml": "on: {workflow_call: {inputs: {devkit-ref: {type: string}}}}\njobs:\n  publish:\n    steps:\n      - run: bash .devkit-checkout/scripts/api-docs-push.sh out url sha\n",
    "marola-site/.github/workflows/notify-umbrella.yml": "on: {push: {branches: [main], paths: [README.md, 'docs/**']}}\njobs:\n  n:\n    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.3.1\n",
    "marola-site/.github/workflows/site.yml": 'on:\n  push: {paths: [marola-image]}\n  repository_dispatch: {types: [site-data-updated]}\njobs:\n  b:\n    steps:\n      - run: git fetch --depth=1 origin site-data\n      - run: echo "marola.dev" > site/dist/CNAME\n      - uses: actions/deploy-pages@v5\n',
    "marola-site/marola-image": "ghcr.io/marola-dev/marola-app:jvm-8a29976@sha256:00\n",
    "marola-site/scripts/board-schema.sh": '# marola-image is read here\nref="$(cat "$root/marola-image")"\n',
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
    "marola-ml/scripts/corpus-fetch.sh": 'pin="$(<"$root/corpus.version")"\nbase="https://github.com/marola-dev/marola-corpus/releases/download"\n',
}


# Each case is rows the fixture's block must hold; a "!" line is text it must not hold.
CASES = {
    "dispatch_types_listened": "| `site-data-updated` | marola-app `ci.yml` (on a push to `main`) | marola-site `site.yml` |\n| `submodule-docs-updated` | marola-site `notify-umbrella.yml` (`notify-umbrella.yml@v0.3.1`, on a push to `main` touching `README.md`, `docs/**`) | marola `pointer-sync.yml` |",
    "dispatch_send_step": "| `site-data-updated` | marola-app `ci.yml` (on a push to `main`) | marola-site `site.yml` |",
    "reusable_workflow_ref_and_event_type": "| `submodule-updated` | marola-app `notify-umbrella.yml` (`notify-umbrella.yml@v0.6.0`, on a push to `main`) | marola `pointer-sync.yml` |\n| a marola-devkit tag | marola-devkit | marola-app `flake.lock` v0.4.1 | `api-docs.yml` v0.4.1 (marola-app); `notify-umbrella.yml` v0.3.1 (marola-site), v0.6.0 (marola-app) |",
    "release_upload_assets": "| `marola-corpus-<tag>.tar.gz` release asset | marola-corpus `release.yml` (on a `v*` tag) | marola-ml `corpus.version` | marola-ml `scripts/corpus-fetch.sh` |",
    "image_publish": "| `ghcr.io/marola-dev/marola-app` image | marola-app `docker.yml` (on a push to `main` touching `corpus.version`) | marola-site `marola-image` | marola-site `scripts/board-schema.sh` |",
    "deploy_pages_site": "| marola `docs.yml` | docs.marola.dev |\n| marola-site `site.yml` | marola.dev |",
    "pin_file_and_reader": "| `marola-corpus-<tag>.tar.gz` release asset | marola-corpus `release.yml` (on a `v*` tag) | marola-ml `corpus.version` | marola-ml `scripts/corpus-fetch.sh` |\n| marola-app `corpus.version` | marola-app `docker.yml` |\n| marola-site `marola-image` | marola-site `site.yml` |",
    "branch_artifact_api_docs": "| `api-docs` branch | marola-app `api-docs.yml` (`api-docs.yml@v0.4.1`, on a push to `main`) | — | marola `scripts/fetch-api-docs.sh` |",
    "branch_artifact_site_data": "| `site-data` branch of marola-site | marola-app `ci.yml` (on a push to `main`) | — | marola-site `site.yml` |\n!pushes to marola-site",
    "compose_image_reader": "| `ghcr.io/marola-dev/marola-ml` image | marola-ml `docker-local.yml` (on a push to `main`) | — | marola-app `docker-compose.yml` |",
    "cross_repo_pr": "| PRs into marola-app: `recommendation_prompt.json`, `review_prompt.json` | marola-ml `compile-prompt.yml` (by hand) | — | marola-app |\n!pushes to marola-app",
    "cross_repo_push": "| pushes to marola-oods | marola-app `ingest.yml` (on a schedule) | — | marola-oods |",
}


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in FIXTURE.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        w = scan(root)
        block = render(w)
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
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    block = render(scan(args.root))
    if args.file:
        write_block(args.file, block)
    else:
        sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
