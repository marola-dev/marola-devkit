#!/usr/bin/env python3
"""merge-key-check: MERGE_BOT_KEY only where MIP-0086 §5.6 allows it.

merge-key-check [ROOT]       # ROOT/.github/workflows against ROOT/.github/merge-key-allowlist
merge-key-check --self-test
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml

KEY = "MERGE_BOT_KEY"
ALLOWLIST = ".github/merge-key-allowlist"
USES_OK = re.compile(
    r"^(actions/checkout|actions/create-github-app-token|actions/download-artifact"
    r"|marola-dev/marola-devkit/\.github/actions/bot-token|\./\.github/actions/bot-token)(@|$)"
)
# Secret names are case-insensitive when referenced, so every presence test is too.
HAS_KEY = re.compile(KEY, re.I)
SHELL_OK = re.compile(r"^(bash|sh)(\s|$)")
# Whatever fetches or builds third-party code must not share a job with the key.
RUN_BAD = re.compile(r"(?<![\w./-])(nix|npm|npx|pip3?|uv|uvx|cargo|sbt|curl|wget)(?![\w.-])")


def allowed(root: Path) -> set[str]:
    f = root / ALLOWLIST
    if not f.is_file():
        return set()
    lines = (ln.strip() for ln in f.read_text().splitlines())
    return {ln for ln in lines if ln and not ln.startswith("#")}


def default_shell(rel: str, where: str, node: dict) -> list[str]:
    sh = ((node.get("defaults") or {}).get("run") or {}).get("shell")
    if sh and not SHELL_OK.match(sh):
        return [f"{rel}: {where}: defaults.run.shell `{sh}` runs beside {KEY}"]
    return []


def problems(root: Path) -> list[str]:
    allow = allowed(root)
    out = [
        f"{ALLOWLIST}: {rel} does not exist" for rel in sorted(allow) if not (root / rel).is_file()
    ]
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        rel = wf.relative_to(root).as_posix()
        text = wf.read_text()
        doc = yaml.safe_load(text) or {}
        if not HAS_KEY.search(text) or rel not in allow:
            if HAS_KEY.search(text):
                out.append(f"{rel}: names {KEY} but is not in {ALLOWLIST}")
            if (root / ALLOWLIST).is_file():
                out += [
                    f"{rel}: job {n}: `secrets: inherit` hands {KEY} to the callee"
                    for n, j in (doc.get("jobs") or {}).items()
                    if j.get("secrets") == "inherit"
                ]
            continue
        if HAS_KEY.search(yaml.safe_dump(doc.get("env") or {})):
            out.append(f"{rel}: workflow-level env hands {KEY} to every job")
        out += default_shell(rel, "workflow", doc)
        for name, job in (doc.get("jobs") or {}).items():
            if job.get("secrets") == "inherit":
                out.append(f"{rel}: job {name}: `secrets: inherit` hands {KEY} to the callee")
            if not HAS_KEY.search(yaml.safe_dump(job)):
                continue
            out += default_shell(rel, f"job {name}", job)
            if job.get("uses"):
                out.append(f"{rel}: job {name}: reusable workflow call runs beside {KEY}")
            for field in ("container", "services"):
                if job.get(field):
                    out.append(f"{rel}: job {name}: `{field}` runs images beside {KEY}")
            for i, step in enumerate(job.get("steps") or [], 1):
                uses = step.get("uses")
                if uses and not USES_OK.match(uses):
                    out.append(f"{rel}: job {name}, step {i}: `{uses}` runs beside {KEY}")
                sh = step.get("shell")
                if sh and not SHELL_OK.match(sh):
                    out.append(f"{rel}: job {name}, step {i}: shell `{sh}` runs beside {KEY}")
                m = RUN_BAD.search(step.get("run") or "")
                if m:
                    out.append(f"{rel}: job {name}, step {i}: `{m.group(1)}` runs beside {KEY}")
    return out


def self_test() -> None:
    def case(files: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".github" / "workflows").mkdir(parents=True)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            return problems(root)

    wf = ".github/workflows/merge.yml"
    clean = (
        "jobs:\n  merge:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "      - id: bot\n        uses: ./.github/actions/bot-token\n"
        "        with:\n          private-key: ${{ secrets.MERGE_BOT_KEY }}\n"
        "      - run: scripts/merge.sh\n"
    )
    keyless = "\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: nix develop --command just build\n"
    assert case({".github/workflows/ci.yml": "jobs: {}\n"}) == [], "no_reference_is_clean"
    got = case({wf: clean})
    assert len(got) == 1 and "not in" in got[0], f"unlisted_file_fails: {got}"
    assert case({wf: clean, ALLOWLIST: f"# merge jobs\n{wf}\n"}) == [], "listed_clean_job_passes"
    got = case({wf: clean + "      - run: nix develop --command true\n", ALLOWLIST: wf})
    assert len(got) == 1 and "`nix`" in got[0], f"nix_beside_key_fails: {got}"
    got = case({wf: clean + "      - uses: some/action@v1\n", ALLOWLIST: wf})
    assert len(got) == 1 and "some/action" in got[0], f"third_party_action_fails: {got}"
    got = case(
        {wf: clean.replace("actions/checkout@v7", "actions/checkout-evil@v1"), ALLOWLIST: wf}
    )
    assert len(got) == 1, f"lookalike_action_fails: {got}"
    assert case({wf: clean + keyless, ALLOWLIST: wf}) == [], "nix_in_a_keyless_job_passes"
    got = case({wf: "env:\n  K: ${{ secrets.MERGE_BOT_KEY }}\n" + clean, ALLOWLIST: wf})
    assert any("workflow-level" in p for p in got), f"workflow_env_fails: {got}"
    got = case({ALLOWLIST: ".github/workflows/gone.yml\n"})
    assert len(got) == 1 and "does not exist" in got[0], f"stale_allowlist_entry_fails: {got}"
    lower = clean.replace(KEY, KEY.lower())
    got = case({wf: lower + "      - run: npm ci\n"})
    assert len(got) == 1 and "not in" in got[0], f"lowercase_unlisted_fails: {got}"
    got = case({wf: lower + "      - run: nix develop\n", ALLOWLIST: wf})
    assert len(got) == 1 and "`nix`" in got[0], f"lowercase_job_checked: {got}"
    call = "\n  call:\n    uses: o/r/.github/workflows/x.yml@v1\n    secrets:\n      k: ${{ secrets.MERGE_BOT_KEY }}\n"
    got = case({wf: clean + call, ALLOWLIST: wf})
    assert len(got) == 1 and "reusable workflow" in got[0], f"reusable_call_fails: {got}"
    inherit = "\n  call:\n    uses: o/r/.github/workflows/x.yml@v1\n    secrets: inherit\n"
    got = case({wf: clean + inherit, ALLOWLIST: wf})
    assert any("inherit" in p for p in got), f"secrets_inherit_fails: {got}"
    got = case(
        {wf: clean.replace("    steps:", "    container: node:20\n    steps:"), ALLOWLIST: wf}
    )
    assert len(got) == 1 and "container" in got[0], f"container_fails: {got}"
    got = case(
        {
            wf: clean.replace(
                "    steps:", "    services:\n      db:\n        image: x\n    steps:"
            ),
            ALLOWLIST: wf,
        }
    )
    assert len(got) == 1 and "services" in got[0], f"services_fails: {got}"
    got = case({wf: clean + "        shell: nix develop --command bash {0}\n", ALLOWLIST: wf})
    assert len(got) == 1 and "shell" in got[0], f"custom_shell_fails: {got}"
    assert case({wf: clean + "        shell: bash -e {0}\n", ALLOWLIST: wf}) == [], (
        "bash_shell_passes"
    )
    got = case({wf: "env:\n  K: ${{ secrets.merge_bot_key }}\n" + clean, ALLOWLIST: wf})
    assert any("workflow-level" in p for p in got), f"lowercase_workflow_env_fails: {got}"
    nixsh = "nix develop --command bash {0}"
    got = case({wf: f"defaults:\n  run:\n    shell: {nixsh}\n" + clean, ALLOWLIST: wf})
    assert any("defaults" in p for p in got), f"workflow_default_shell_fails: {got}"
    got = case(
        {
            wf: clean.replace(
                "    steps:", f"    defaults:\n      run:\n        shell: {nixsh}\n    steps:"
            ),
            ALLOWLIST: wf,
        }
    )
    assert any("defaults" in p for p in got), f"job_default_shell_fails: {got}"
    other = ".github/workflows/other.yml"
    plain = "jobs:\n  c:\n    uses: o/r/.github/workflows/x.yml@v1\n    secrets: inherit\n"
    got = case({wf: clean, other: plain, ALLOWLIST: wf})
    assert len(got) == 1 and other in got[0] and "inherit" in got[0], (
        f"unlisted_inherit_fails: {got}"
    )
    assert case({other: plain}) == [], "inherit_without_allowlist_passes"
    assert not RUN_BAD.search("set -euo pipefail\nnixpkgs=1 scripts/x.sh /nix/store/y"), (
        "no_false_positive"
    )
    print("merge-key-check self-test: ok")


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return 0
    found = problems(Path(a.root))
    for p in found:
        print(f"merge-key-check: {p}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
