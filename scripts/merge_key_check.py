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
# Whatever fetches or builds third-party code must not share a job with the key.
RUN_BAD = re.compile(r"(?<![\w./-])(nix|npm|npx|pip3?|uv|uvx|cargo|sbt|curl|wget)(?![\w.-])")


def allowed(root: Path) -> set[str]:
    f = root / ALLOWLIST
    if not f.is_file():
        return set()
    lines = (ln.strip() for ln in f.read_text().splitlines())
    return {ln for ln in lines if ln and not ln.startswith("#")}


def problems(root: Path) -> list[str]:
    allow = allowed(root)
    out = [
        f"{ALLOWLIST}: {rel} does not exist" for rel in sorted(allow) if not (root / rel).is_file()
    ]
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        rel = wf.relative_to(root).as_posix()
        text = wf.read_text()
        if KEY not in text:
            continue
        if rel not in allow:
            out.append(f"{rel}: names {KEY} but is not in {ALLOWLIST}")
            continue
        doc = yaml.safe_load(text) or {}
        if KEY in yaml.safe_dump(doc.get("env") or {}):
            out.append(f"{rel}: workflow-level env hands {KEY} to every job")
        for name, job in (doc.get("jobs") or {}).items():
            if KEY not in yaml.safe_dump(job):
                continue
            for i, step in enumerate(job.get("steps") or [], 1):
                uses = step.get("uses")
                if uses and not USES_OK.match(uses):
                    out.append(f"{rel}: job {name}, step {i}: `{uses}` runs beside {KEY}")
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
