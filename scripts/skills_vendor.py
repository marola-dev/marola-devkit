#!/usr/bin/env python3
"""Pin vendored skills to an upstream commit in skills.lock (MIP-0080).

The lock is <repo>/.claude/skills/skills.lock unless --lock says otherwise; a skill's files live in
<lock dir>/<name>/. `check` is offline; the rest read the upstream through a blobless git clone.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_LOCK = Path(".claude/skills/skills.lock")
LICENSES = [
    (r"\bMIT License\b", "MIT"),
    (r"Apache License.*Version 2\.0", "Apache-2.0"),
    (r"BSD 3-Clause|Redistributions in binary form.*Neither the name", "BSD-3-Clause"),
]


def git(args: list[str], cwd: Path | None = None, binary: bool = False) -> bytes | str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if out.returncode:
        sys.exit(f"git {' '.join(args)}: {out.stderr.decode().strip()}")
    return out.stdout if binary else out.stdout.decode()


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def local_files(skill_dir: Path) -> dict[str, str]:
    files = sorted(p for p in skill_dir.rglob("*") if p.is_file())
    return {p.relative_to(skill_dir).as_posix(): blob_sha(p.read_bytes()) for p in files}


def upstream_path(entry: dict, rel: str) -> str:
    override = (entry.get("paths") or {}).get(rel)
    if override is not None:
        return override
    return f"{entry['path']}/{rel}" if entry["path"] else rel


def lock_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return (Path(out.stdout.strip()) if out.returncode == 0 else Path.cwd()) / DEFAULT_LOCK


def read_lock(path: Path) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("version") != 1 or not isinstance(lock.get("skills"), dict):
        sys.exit(f"{path}: not a version 1 skills.lock")
    return lock


def write_lock(path: Path, lock: dict) -> None:
    lock["skills"] = dict(sorted(lock["skills"].items()))
    path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def detect_license(skill_dir: Path) -> str | None:
    skill = skill_dir / "SKILL.md"
    if skill.is_file():
        m = re.search(r"^license:\s*['\"]?([\w.+-]+)['\"]?\s*$", skill.read_text("utf-8"), re.M)
        if m:
            return m.group(1)
    for f in sorted(skill_dir.glob("LICENSE*")):
        text = f.read_text("utf-8", errors="replace")
        return next((spdx for pat, spdx in LICENSES if re.search(pat, text, re.S)), None)
    return None


class Upstream:
    """A blobless clone of `owner/repo` (or a URL, for the self-test), one per process."""

    clones: dict[str, Upstream] = {}

    def __init__(self, repo: str) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="skills-vendor-")) / "clone"
        url = repo if "://" in repo else f"https://github.com/{repo}"
        git(["clone", "-q", "--filter=blob:none", url, str(self.dir)])

    @classmethod
    def of(cls, repo: str) -> Upstream:
        return cls.clones.setdefault(repo, cls(repo))

    def commits(self, path: str, limit: int, min_age: int) -> list[tuple[str, datetime]]:
        cmd = ["log", f"-{limit}", "--format=%H %cI"]
        if min_age:
            cmd.append(f"--until={(datetime.now(UTC) - timedelta(days=min_age)).isoformat()}")
        lines = git([*cmd, "--", path or "."], self.dir).splitlines()
        return [(s, datetime.fromisoformat(d)) for s, d in (ln.split() for ln in lines)]

    def tree(self, sha: str) -> dict[str, str]:
        lines = git(["ls-tree", "-r", sha], self.dir).splitlines()
        return {ln.split("\t", 1)[1]: ln.split()[2] for ln in lines}

    def is_ancestor(self, a: str, b: str) -> bool:
        return (
            subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=self.dir).returncode
            == 0
        )

    def blob(self, sha: str, path: str) -> bytes:
        return git(["cat-file", "blob", f"{sha}:{path}"], self.dir, binary=True)


def check_problems(lock_file: Path) -> list[str]:
    problems = []
    for name, entry in read_lock(lock_file)["skills"].items():
        have, want = local_files(lock_file.parent / name), entry["files"]
        for rel in sorted(set(have) | set(want)):
            if rel not in want:
                problems.append(f"{name}/{rel}: not in the lock")
            elif rel not in have:
                problems.append(f"{name}/{rel}: missing")
            elif have[rel] != want[rel]:
                problems.append(f"{name}/{rel}: changed ({have[rel][:7]} != {want[rel][:7]})")
    return problems


def file_diff(old: bytes | None, new: bytes | None, label: str) -> str:
    try:
        a = old.decode("utf-8").splitlines(keepends=True) if old is not None else []
        b = new.decode("utf-8").splitlines(keepends=True) if new is not None else []
    except UnicodeDecodeError:
        return f"Binary file {label} differs\n"
    return "".join(difflib.unified_diff(a, b, f"a/{label}", f"b/{label}"))


def skill_report(lock_file: Path, name: str, entry: dict, min_age: int, sha: str | None) -> dict:
    """What `target` changes in the vendored files, against the pinned upstream blobs."""
    if entry.get("hold"):
        return {"name": name, "status": "held", "reason": entry["hold"]}
    up = Upstream.of(entry["upstream"])
    found = [(sha, datetime.now(UTC))] if sha else up.commits(entry["path"], 1, min_age)
    if not found:
        return {"name": name, "status": "no upstream commit", "reason": "none old enough"}
    # A pin a person moved past --min-age by hand is newer, not behind: never step it back.
    if not sha and entry.get("commit") and up.is_ancestor(found[0][0], entry["commit"]):
        pin = entry["commit"]
        found = [
            (pin, datetime.fromisoformat(git(["log", "-1", "--format=%cI", pin], up.dir).strip()))
        ]
    (target, date), pinned = found[0], entry.get("upstream_files") or entry["files"]
    tree, changed, diff = up.tree(target), [], ""
    for rel in sorted(entry["files"]):
        path = upstream_path(entry, rel)
        if tree.get(path) == pinned.get(rel):
            continue
        changed.append(rel)
        old_at = entry.get("commit")
        old = up.blob(old_at, path) if old_at else (lock_file.parent / name / rel).read_bytes()
        diff += file_diff(old, up.blob(target, path) if path in tree else None, f"{name}/{rel}")
    return {
        "name": name,
        "status": "behind" if changed else "current",
        "pinned": entry.get("commit"),
        "upstream": target,
        "date": date.date().isoformat(),
        "changed": changed,
        "diff": diff,
    }


def print_report(r: dict) -> None:
    if "upstream" in r:
        print(f"{r['name']}: {r['status']} (upstream {r['upstream'][:7]}, {r['date']})")
        print(r["diff"], end="")
    else:
        print(f"{r['name']}: {r['status']}, {r['reason']}")


def update_skill(lock_file: Path, name: str, entry: dict, r: dict) -> None:
    up, skill_dir = Upstream.of(entry["upstream"]), lock_file.parent / name
    tree, upstream_files = up.tree(r["upstream"]), {}
    for rel in sorted(entry["files"]):
        path = upstream_path(entry, rel)
        if path not in tree:
            sys.exit(f"{name}/{rel}: gone upstream at {r['upstream'][:7]}; drop or hold it")
        (skill_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (skill_dir / rel).write_bytes(up.blob(r["upstream"], path))
        upstream_files[rel] = tree[path]
    entry.pop("upstream_files", None)
    if patch := entry.get("local_edits"):
        # Run from the lock dir: `git apply` resolves the patch's paths from there, in a repo or not.
        out = subprocess.run(["git", "apply", patch], cwd=lock_file.parent, capture_output=True)
        if out.returncode:
            sys.exit(f"{name}: local_edits {patch} does not apply:\n{out.stderr.decode().strip()}")
        entry["upstream_files"] = upstream_files
    entry["commit"], entry["files"] = r["upstream"], local_files(skill_dir)


def cmd_update(lock_file: Path, names: list[str], sha: str | None, min_age: int, out: str | None):
    lock = read_lock(lock_file)
    names = names or list(lock["skills"])
    if sha and len(names) != 1:
        sys.exit("--sha pins one skill at a time")
    if unknown := [n for n in names if n not in lock["skills"]]:
        sys.exit(f"not in {lock_file}: {', '.join(unknown)}")
    report = ""
    for name in names:
        entry = lock["skills"][name]
        r = skill_report(lock_file, name, entry, min_age, sha)
        if "upstream" not in r or (r["upstream"] == entry.get("commit") and not sha):
            print_report(r)
            continue
        update_skill(lock_file, name, entry, r)
        head = f"{name}: {(r['pinned'] or 'unpinned')[:7]} -> {r['upstream'][:7]}"
        report += f"### {head} ({r['date']})\n\n```diff\n{r['diff'].rstrip()}\n```\n\n"
        print(head)
    write_lock(lock_file, lock)
    if out:  # a PR body holds 64 kB
        Path(out).write_text(
            report[:60000] + ("\n```\n(truncated)\n" if len(report) > 60000 else "")
        )
    return 0


def cmd_init(lock_file: Path, name: str, repo: str, path: str, sha: str | None, paths: list[str]):
    skill_dir = lock_file.parent / name
    if not (files := local_files(skill_dir)):
        sys.exit(f"{skill_dir}: no files")
    lock = read_lock(lock_file) if lock_file.is_file() else {"version": 1, "skills": {}}
    entry = {
        "upstream": repo,
        "path": path.strip("/"),
        "commit": sha,
        "files": files,
        "license": detect_license(skill_dir),
        "local_edits": None,
        "hold": None,
    }
    if paths:
        entry["paths"] = dict(p.split("=", 1) for p in paths)
    if not sha:
        up = Upstream.of(repo)
        for c, _ in up.commits(entry["path"], 200, 0):
            tree = up.tree(c)
            if all(tree.get(upstream_path(entry, rel)) == s for rel, s in files.items()):
                entry["commit"] = c
                break
        else:
            print(f"{name}: none of the last 200 commits matches; commit is null", file=sys.stderr)
    lock["skills"][name] = entry
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    write_lock(lock_file, lock)
    print(f"{name}: {(entry['commit'] or 'null')[:7]}, {len(files)} file(s), {entry['license']}")
    return 0


def self_test() -> int:
    import contextlib
    import io

    def commit(repo: Path, files: dict[str, bytes], days_ago: int) -> str:
        for rel, data in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_bytes(data)
        os.environ["GIT_COMMITTER_DATE"] = str(datetime.now(UTC) - timedelta(days=days_ago))
        git(["add", "-A"], repo)
        git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], repo)
        return git(["rev-parse", "HEAD"], repo).strip()

    def outdated(min_age: int) -> dict:
        return skill_report(lock_file, "x", read_lock(lock_file)["skills"]["x"], min_age, None)

    def set_field(key: str, value: str | None) -> None:
        lock = read_lock(lock_file)
        lock["skills"]["x"][key] = value
        write_lock(lock_file, lock)

    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        tmp, lic = Path(tmp), b"MIT License\n\nCopyright (c) 2026 Someone\n"
        old, new = b"---\nname: x\n---\nold\n", b"---\nname: x\n---\nnew\n"
        up = tmp / "up"
        git(["init", "-q", str(up)])
        commit(up, {"README.md": b"hi\n", "LICENSE": lic}, 60)
        sha_a = commit(up, {"skills/x/SKILL.md": old}, 30)
        sha_b = commit(up, {"skills/x/SKILL.md": new}, 2)
        # The lock sits in a repo's subdirectory, as ww3-gpu's does: git apply runs from there.
        git(["init", "-q", str(tmp / "repo")])
        lock_file = tmp / "repo" / "sub" / "skills.lock"
        (lock_file.parent / "x").mkdir(parents=True)
        (lock_file.parent / "x" / "SKILL.md").write_bytes(old)
        (lock_file.parent / "x" / "LICENSE").write_bytes(lic)

        assert cmd_init(lock_file, "x", up.as_uri(), "skills/x", None, ["LICENSE=LICENSE"]) == 0
        e = read_lock(lock_file)["skills"]["x"]
        assert e["commit"] == sha_a and e["license"] == "MIT", e
        assert sorted(e["files"]) == ["LICENSE", "SKILL.md"], e
        assert check_problems(lock_file) == []

        (lock_file.parent / "x" / "SKILL.md").write_bytes(b"edited\n")
        (lock_file.parent / "x" / "extra").write_bytes(b"?")
        changed, extra = check_problems(lock_file)
        assert changed.startswith("x/SKILL.md: changed") and extra == "x/extra: not in the lock"
        (lock_file.parent / "x" / "extra").unlink()
        (lock_file.parent / "x" / "SKILL.md").write_bytes(old)

        r = outdated(0)
        assert r["status"] == "behind" and r["changed"] == ["SKILL.md"] and "+new" in r["diff"], r
        assert outdated(14)["status"] == "current", "a 2-day-old commit is too young for 14"

        assert cmd_update(lock_file, [], None, 14, None) == 0
        assert read_lock(lock_file)["skills"]["x"]["commit"] == sha_a
        report = tmp / "report.md"
        assert cmd_update(lock_file, ["x"], None, 0, str(report)) == 0
        assert read_lock(lock_file)["skills"]["x"]["commit"] == sha_b
        assert (lock_file.parent / "x" / "SKILL.md").read_bytes() == new
        assert f"### x: {sha_a[:7]} -> {sha_b[:7]}" in report.read_text()
        assert "+new" in report.read_text()
        assert check_problems(lock_file) == []
        assert outdated(14)["status"] == "current", "a pin newer than --min-age was stepped back"
        assert cmd_update(lock_file, [], None, 14, None) == 0
        assert read_lock(lock_file)["skills"]["x"]["commit"] == sha_b

        # local_edits: `files` holds the edited shas, `upstream_files` the pristine ones.
        hdr = "--- a/x/SKILL.md\n+++ b/x/SKILL.md\n"
        hunk = "@@ -1,4 +1,4 @@\n ---\n name: x\n ---\n-new\n+new, but ours\n"
        (lock_file.parent / "x.patch").write_text(hdr + hunk)
        set_field("local_edits", "x.patch")
        assert cmd_update(lock_file, ["x"], sha_b, 0, None) == 0
        e = read_lock(lock_file)["skills"]["x"]
        assert (lock_file.parent / "x" / "SKILL.md").read_text().endswith("new, but ours\n")
        assert e["upstream_files"]["SKILL.md"] == blob_sha(new) != e["files"]["SKILL.md"]
        assert check_problems(lock_file) == []
        assert outdated(0)["status"] == "current", "an edited copy at upstream HEAD is current"

        set_field("hold", "waiting on licence check")
        assert outdated(0)["status"] == "held"

        (lock_file.parent / "x.patch").write_text(hdr + "@@ -1 +1 @@\n-nope\n+never\n")
        set_field("hold", None)
        try:
            cmd_update(lock_file, ["x"], sha_a, 0, None)
            raise AssertionError("a patch that does not apply must fail")
        except SystemExit as exc:
            assert "does not apply" in str(exc), exc
    print("skills-vendor self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lock", help=f"default: <repo>/{DEFAULT_LOCK}")
    common.add_argument("--git", action="store_true", help=argparse.SUPPRESS)  # a no-op since 0.6.0
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("check", parents=[common], help="every vendored file matches the lock (offline)")
    p = sub.add_parser("outdated", parents=[common], help="what upstream changed since the pin")
    p.add_argument("--min-age", type=int, default=0, metavar="DAYS", help="skip younger commits")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="exit 1 when a skill is behind")
    p = sub.add_parser("update", parents=[common], help="re-vendor, re-apply local_edits, re-pin")
    p.add_argument("names", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--sha", help="pin this commit (one skill)")
    p.add_argument("--min-age", type=int, default=0, metavar="DAYS")
    p.add_argument("--report", metavar="FILE", help="write the diffs as Markdown")
    p = sub.add_parser("init", parents=[common], help="a lock entry from the copy in the tree")
    p.add_argument("name")
    p.add_argument("--upstream", required=True, metavar="OWNER/REPO")
    p.add_argument("--path", required=True, metavar="DIR", help="'' for the repo root")
    p.add_argument("--sha", help="else the newest of 200 commits whose tree matches")
    p.add_argument("--paths", action="append", default=[], metavar="FILE=UPSTREAM")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.cmd:
        ap.error("check, outdated, update or init")
    lock_file = lock_path(a.lock)
    if a.cmd == "check":
        if not lock_file.is_file():
            print(f"skills-vendor: no {lock_file}, nothing to check")
            return 0
        problems = check_problems(lock_file)
        sys.stderr.write("".join(f"{p}\n" for p in problems))
        print(f"skills-vendor: {lock_file}: {len(problems)} problem(s)")
        return 1 if problems else 0
    if a.cmd == "outdated":
        skills = read_lock(lock_file)["skills"].items()
        reports = [skill_report(lock_file, n, e, a.min_age, None) for n, e in skills]
        if a.json:
            print(json.dumps(reports, indent=2))
        else:
            for r in reports:
                print_report(r)
        return 1 if a.strict and any(r["status"] == "behind" for r in reports) else 0
    if a.cmd == "update":
        if not a.names and not a.all:
            ap.error("update needs <name>... or --all")
        return cmd_update(lock_file, a.names, a.sha, a.min_age, a.report)
    return cmd_init(lock_file, a.name, a.upstream, a.path, a.sha, a.paths)


if __name__ == "__main__":
    sys.exit(main())
