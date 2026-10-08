#!/usr/bin/env python3
"""Release the devkit: `just release X.Y.Z [--dry-run]`.

Two runs of the same command, with a human merge between them:
- versions on main are not X.Y.Z yet: write X.Y.Z into every location (`plugin.json`, the flake
  package, the documented pins) and a CHANGELOG heading on `chore/release-vX.Y.Z`, then push it
  for a PR;
- main already says X.Y.Z everywhere: tag main `vX.Y.Z` and push the tag; release.yml publishes
  the GitHub release from it.

`--check [X.Y.Z]` only verifies every location agrees (release.yml runs it against the tag).
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

SEMVER = r"\d+\.\d+\.\d+"
VERSION_FILES = {
    "plugins/marola-devkit/.claude-plugin/plugin.json": re.compile(rf'("version": ")({SEMVER})(")'),
    "flake.nix": re.compile(rf'(version = ")({SEMVER})(";)'),
}
# A pin is a v-tag in one of these contexts; actions/checkout@v7 and history in CHANGELOG are not.
PIN = re.compile(
    rf"(marola-devkit/v|marola-devkit/\.github/workflows/[\w.-]+@v|devkit-ref: v"
    rf'|marola-devkit", "ref": "v|\*\*Status:\*\* this is v)({SEMVER})()'
)
PIN_GLOBS = ["README.md", "docs/**/*.md", ".github/workflows/*.yml"]


def locations(root):
    for rel, rx in VERSION_FILES.items():
        yield root / rel, rx
    for g in PIN_GLOBS:
        for p in sorted(root.glob(g)):
            yield p, PIN


def found(root):
    """Every (file, version) the release touches."""
    return [
        (p.relative_to(root).as_posix(), m.group(2))
        for p, rx in locations(root)
        for m in rx.finditer(p.read_text())
    ]


def mismatches(root, version):
    hits = found(root)
    missing = [f for f in VERSION_FILES if f not in {h[0] for h in hits}]
    return [f"{f}: no version found" for f in missing] + [
        f"{f}: {v}" for f, v in hits if v != version
    ]


def bump(root, version):
    for p, rx in locations(root):
        text = p.read_text()
        new = rx.sub(lambda m: m.group(1) + version + m.group(3), text)
        if new != text:
            p.write_text(new)


def changelog(root, version, date, subjects):
    p = root / "CHANGELOG.md"
    text = p.read_text()
    if f"## v{version} " in text:
        return
    entry = f"## v{version} — {date}\n\n" + "".join(f"- {s}\n" for s in subjects) + "\n"
    i = text.find("\n## ")
    p.write_text(text[: i + 1] + entry + text[i + 1 :] if i >= 0 else text + "\n" + entry)


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def die(msg):
    sys.exit(f"release: {msg}")


def release(version, dry_run):
    root = Path(git("rev-parse", "--show-toplevel"))
    tag = f"v{version}"
    if git("branch", "--show-current", cwd=root) != "main":
        die("check out main first")
    if git("status", "--porcelain", "--untracked-files=no", cwd=root):
        die("the working tree is dirty; commit or stash first")
    git("fetch", "-q", "--tags", "origin", "main", cwd=root)
    if git("rev-parse", "HEAD", cwd=root) != git("rev-parse", "origin/main", cwd=root):
        die("main is not level with origin/main; pull or push first")
    if git("tag", "-l", tag, cwd=root):
        die(f"tag {tag} already exists")
    prev = (git("tag", "-l", "v*", "--sort=-v:refname", cwd=root).splitlines() or [""])[0]

    if not mismatches(root, version):
        if f"## {tag} " not in (root / "CHANGELOG.md").read_text():
            die(f"CHANGELOG.md has no '## {tag}' heading")
        print(
            f"release: {tag} at {git('log', '-1', '--format=%h %s', cwd=root)} (previous: {prev or 'none'})",
            file=sys.stderr,
        )
        cmds = [["tag", "-a", tag, "-m", f"marola-devkit {tag}"], ["push", "origin", tag]]
        for c in cmds:
            print("+ git " + " ".join(c), file=sys.stderr)
            if not dry_run:
                git(*c, cwd=root)
        if not dry_run:
            print(
                f"release: pushed {tag}; release.yml publishes the GitHub release.", file=sys.stderr
            )
        return

    branch = f"chore/release-{tag}"
    log_range = f"{prev}..HEAD" if prev else "HEAD"
    subjects = git("log", "--no-merges", "--format=%s", log_range, cwd=root).splitlines()
    if dry_run:
        files = sorted({f for f, _ in found(root)})
        print(f"release: would set {version} in {', '.join(files)} on {branch}", file=sys.stderr)
        return
    git("switch", "-c", branch, cwd=root)
    bump(root, version)
    changelog(root, version, git("log", "-1", "--format=%cs", cwd=root), subjects)
    git(
        "commit",
        "-aqm",
        f"release: {tag}\n\nTested: python3 scripts/release.py --check {version}\nCost: n/a (release script)",
        cwd=root,
    )
    git("push", "-u", "origin", branch, cwd=root)
    print(
        f"release: pushed {branch}. Edit the CHANGELOG entry into prose, open the PR (`just pr`), "
        f"and after it merges run `just release {version}` again on main to tag it.",
        file=sys.stderr,
    )


def self_test():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        files = {
            "plugins/marola-devkit/.claude-plugin/plugin.json": '{\n  "version": "0.5.1",\n}\n',
            "flake.nix": 'version = "0.5.1";\n',
            "README.md": (
                "**Status:** this is v0.5.1; each repo\n"
                'url = "github:marola-dev/marola-devkit/v0.5.1";\n'
                '"source": { "source": "github", "repo": "marola-dev/marola-devkit", "ref": "v0.4.0" }\n'
                "- uses: actions/checkout@v7.0.0\n"
            ),
            "docs/4-reference_workflows.md": (
                "uses: marola-dev/marola-devkit/.github/workflows/scala-ci.yml@v0.5.1\n  devkit-ref: v0.5.1\n"
            ),
            ".github/workflows/notify-umbrella.yml": "#   uses: marola-dev/marola-devkit/.github/workflows/n.yml@v0.5.1\n",
            "CHANGELOG.md": "# Changelog\n\nIntro.\n\n## v0.5.1 — 2026-10-07\n\n- old\n",
        }
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        assert len(found(root)) == 8, found(root)
        assert any("0.4.0" in m for m in mismatches(root, "0.5.1")), "a stale pin went unnoticed"
        bump(root, "0.6.0")
        assert not mismatches(root, "0.6.0"), mismatches(root, "0.6.0")
        assert "actions/checkout@v7.0.0" in (root / "README.md").read_text(), (
            "a third-party pin moved"
        )
        changelog(root, "0.6.0", "2026-10-08", ["feat: a"])
        changelog(root, "0.6.0", "2026-10-08", ["feat: a"])
        cl = (root / "CHANGELOG.md").read_text()
        assert cl.count("## v0.6.0") == 1 and cl.index("## v0.6.0") < cl.index("## v0.5.1"), cl
        assert "Intro.\n\n## v0.6.0 — 2026-10-08\n\n- feat: a\n\n## v0.5.1" in cl, cl
        (root / "flake.nix").write_text("nothing\n")
        assert "flake.nix: no version found" in mismatches(root, "0.6.0")
    print("release: self-test ok", file=sys.stderr)


def main(argv):
    if argv[:1] == ["--self-test"]:
        return self_test()
    if argv[:1] == ["--check"]:
        root = Path(git("rev-parse", "--show-toplevel"))
        version = (argv[1] if len(argv) > 1 else "").removeprefix("v")
        version = version or VERSION_FILES["flake.nix"].search(
            (root / "flake.nix").read_text()
        ).group(2)
        bad = mismatches(root, version)
        for b in bad:
            print(f"release: expected {version}, {b}", file=sys.stderr)
        sys.exit(1 if bad else 0)
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1 or not re.fullmatch(rf"v?{SEMVER}", args[0]):
        die("usage: just release <X.Y.Z> [--dry-run]")
    release(args[0].removeprefix("v"), "--dry-run" in argv)


if __name__ == "__main__":
    main(sys.argv[1:])
