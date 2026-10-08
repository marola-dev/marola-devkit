#!/usr/bin/env python3
"""Pin vendored skills to an upstream commit in skills.lock, and keep them there.

skills-vendor check [--lock PATH]
skills-vendor outdated [--lock PATH] [--min-age DAYS] [--json] [--strict] [--git]
skills-vendor update (<name>... | --all) [--sha SHA] [--min-age DAYS] [--report FILE] [--git]
skills-vendor init <name> --upstream owner/repo --path DIR [--sha SHA] [--git]
skills-vendor --self-test

The lock is `<repo>/.claude/skills/skills.lock` unless `--lock` says otherwise; a skill's files
live in `<lock dir>/<name>/`. `check` is offline. The rest read the upstream through GitHub's REST
API (`GH_TOKEN`/`GITHUB_TOKEN` when set) or, with `--git`, a blobless clone, for a host that
blocks api.github.com but not github.com.
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
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_LOCK = Path(".claude/skills/skills.lock")
SHA = re.compile(r"^[0-9a-f]{40}$")
LICENSES = [
    (re.compile(r"\bMIT License\b"), "MIT"),
    (re.compile(r"Apache License.*Version 2\.0", re.S), "Apache-2.0"),
    (
        re.compile(r"BSD 3-Clause|Redistributions in binary form.*Neither the name", re.S),
        "BSD-3-Clause",
    ),
    (re.compile(r"BSD 2-Clause"), "BSD-2-Clause"),
    (re.compile(r"GNU GENERAL PUBLIC LICENSE.*Version 3", re.S), "GPL-3.0"),
    (re.compile(r"Creative Commons.*Attribution 4\.0", re.S), "CC-BY-4.0"),
]


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def local_files(skill_dir: Path) -> dict[str, str]:
    return {
        p.relative_to(skill_dir).as_posix(): blob_sha(p.read_bytes())
        for p in sorted(skill_dir.rglob("*"))
        if p.is_file()
    }


def upstream_path(entry: dict, rel: str) -> str:
    """Where a vendored file sits in the upstream tree: `path/<rel>` unless `paths` says otherwise."""
    override = (entry.get("paths") or {}).get(rel)
    if override is not None:
        return override
    base = entry["path"].strip("/")
    return f"{base}/{rel}" if base else rel


def lock_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    root = Path(out.stdout.strip()) if out.returncode == 0 else Path.cwd()
    return root / DEFAULT_LOCK


def read_lock(path: Path) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("version") != 1 or not isinstance(lock.get("skills"), dict):
        sys.exit(f"{path}: not a version 1 skills.lock")
    return lock


def write_lock(path: Path, lock: dict) -> None:
    lock["skills"] = dict(sorted(lock["skills"].items()))
    path.write_text(json.dumps(lock, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def detect_license(skill_dir: Path) -> str | None:
    skill = skill_dir / "SKILL.md"
    if skill.is_file():
        m = re.search(r"^license:\s*['\"]?([\w.+-]+)['\"]?\s*$", skill.read_text("utf-8"), re.M)
        if m:
            return m.group(1)
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
        f = skill_dir / name
        if f.is_file():
            text = f.read_text("utf-8", errors="replace")
            for pat, spdx in LICENSES:
                if pat.search(text):
                    return spdx
    return None


# --- fetchers -------------------------------------------------------------------------------------


class Commit:
    def __init__(self, sha: str, date: datetime) -> None:
        self.sha, self.date = sha, date


class RestFetcher:
    """GitHub's REST API for commits and trees, raw.githubusercontent.com for content."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        self._trees: dict[str, dict[str, str]] = {}

    def _get(self, url: str, raw: bool = False) -> bytes:
        headers = {
            "User-Agent": "skills-vendor",
            "Accept": "application/octet-stream" if raw else "application/vnd.github+json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            sys.exit(f"{url}: HTTP {err.code} {err.reason} (api.github.com blocked? try --git)")

    def commits(self, path: str, limit: int, until: datetime | None = None) -> list[Commit]:
        out: list[Commit] = []
        page = 1
        while len(out) < limit:
            q = {"per_page": min(100, limit - len(out)), "page": page}
            if path:
                q["path"] = path
            if until:
                q["until"] = until.isoformat()
            data = json.loads(
                self._get(
                    f"https://api.github.com/repos/{self.repo}/commits?{urllib.parse.urlencode(q)}"
                )
            )
            if not data:
                break
            out += [
                Commit(
                    c["sha"],
                    datetime.fromisoformat(c["commit"]["committer"]["date"].replace("Z", "+00:00")),
                )
                for c in data
            ]
            page += 1
        return out[:limit]

    def tree(self, sha: str) -> dict[str, str]:
        if sha not in self._trees:
            data = json.loads(
                self._get(f"https://api.github.com/repos/{self.repo}/git/trees/{sha}?recursive=1")
            )
            self._trees[sha] = {t["path"]: t["sha"] for t in data["tree"] if t["type"] == "blob"}
        return self._trees[sha]

    def blob(self, sha: str, path: str) -> bytes:
        return self._get(f"https://raw.githubusercontent.com/{self.repo}/{sha}/{path}", raw=True)


class GitFetcher:
    """A blobless clone in a temp dir: works wherever `git clone https://github.com/...` does."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.dir = Path(tempfile.mkdtemp(prefix="skills-vendor-")) / "clone"
        self._run(
            [
                "git",
                "clone",
                "-q",
                "--filter=blob:none",
                f"https://github.com/{repo}",
                str(self.dir),
            ],
            cwd=None,
        )

    def _run(self, cmd: list[str], cwd: Path | None = ...) -> str:  # type: ignore[assignment]
        out = subprocess.run(
            cmd, cwd=self.dir if cwd is ... else cwd, capture_output=True, text=True, check=False
        )
        if out.returncode:
            sys.exit(f"{' '.join(cmd)}: {out.stderr.strip()}")
        return out.stdout

    def commits(self, path: str, limit: int, until: datetime | None = None) -> list[Commit]:
        cmd = ["git", "log", f"-{limit}", "--format=%H %cI"]
        if until:
            cmd.append(f"--until={until.isoformat()}")
        cmd += ["--", path or "."]
        return [
            Commit(s, datetime.fromisoformat(d))
            for s, d in (ln.split() for ln in self._run(cmd).splitlines())
        ]

    def tree(self, sha: str) -> dict[str, str]:
        out = self._run(["git", "ls-tree", "-r", sha])
        return {ln.split("\t", 1)[1]: ln.split()[2] for ln in out.splitlines()}

    def blob(self, sha: str, path: str) -> bytes:
        out = subprocess.run(
            ["git", "cat-file", "blob", f"{sha}:{path}"],
            cwd=self.dir,
            capture_output=True,
            check=False,
        )
        if out.returncode:
            sys.exit(f"{self.repo}@{sha[:7]}:{path}: {out.stderr.decode().strip()}")
        return out.stdout


def make_fetcher(repo: str, git: bool):
    return GitFetcher(repo) if git else RestFetcher(repo)


# --- commands -------------------------------------------------------------------------------------


def cmd_check(lock_file: Path) -> int:
    if not lock_file.is_file():
        print(f"skills-vendor: no {lock_file}, nothing to check")
        return 0
    lock = read_lock(lock_file)
    problems: list[str] = []
    for name, entry in lock["skills"].items():
        skill_dir = lock_file.parent / name
        if not skill_dir.is_dir():
            problems.append(f"{name}: directory missing")
            continue
        have, want = local_files(skill_dir), entry["files"]
        for rel in sorted(set(have) | set(want)):
            if rel not in want:
                problems.append(f"{name}/{rel}: not in the lock")
            elif rel not in have:
                problems.append(f"{name}/{rel}: missing")
            elif have[rel] != want[rel]:
                problems.append(f"{name}/{rel}: changed ({have[rel][:7]} != {want[rel][:7]})")
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(
            f"skills-vendor: {len(problems)} problem(s); `skills-vendor update <name>` or `init` to re-pin",
            file=sys.stderr,
        )
        return 1
    print(f"skills-vendor: {len(lock['skills'])} skill(s) match {lock_file}")
    return 0


def pinned_upstream(entry: dict) -> dict[str, str]:
    return entry.get("upstream_files") or entry["files"]


def target_commit(fetcher, entry: dict, min_age: int) -> Commit | None:
    """The newest upstream commit touching `path` that is at least `min_age` days old."""
    until = datetime.now(UTC) - timedelta(days=min_age) if min_age else None
    found = fetcher.commits(entry["path"], 1, until)
    return found[0] if found else None


def is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\0" not in data


def file_diff(old: bytes | None, new: bytes | None, label: str) -> str:
    if old == new:
        return ""
    if (old is not None and not is_text(old)) or (new is not None and not is_text(new)):
        return f"Binary file {label} differs\n"
    a = old.decode("utf-8").splitlines(keepends=True) if old is not None else []
    b = new.decode("utf-8").splitlines(keepends=True) if new is not None else []
    return "".join(difflib.unified_diff(a, b, f"a/{label}", f"b/{label}"))


def skill_report(fetcher, name: str, entry: dict, target: Commit, skill_dir: Path) -> dict:
    """Compare the pinned upstream blobs with `target`'s; the diff is per vendored file."""
    tree = fetcher.tree(target.sha)
    pinned = pinned_upstream(entry)
    changed, diff = [], ""
    for rel in sorted(entry["files"]):
        up = upstream_path(entry, rel)
        new_sha = tree.get(up)
        if new_sha == pinned.get(rel):
            continue
        changed.append(rel)
        if entry.get("commit"):
            old = fetcher.blob(entry["commit"], up) if pinned.get(rel) else None
        else:
            f = skill_dir / rel
            old = f.read_bytes() if f.is_file() else None
        new = fetcher.blob(target.sha, up) if new_sha else None
        diff += file_diff(old, new, f"{name}/{rel}")
    status = "behind" if changed else "current"
    return {
        "name": name,
        "status": status,
        "pinned": entry.get("commit"),
        "upstream": target.sha,
        "date": target.date.date().isoformat(),
        "changed": changed,
        "diff": diff,
    }


def cmd_outdated(lock_file: Path, min_age: int, as_json: bool, strict: bool, git: bool) -> int:
    lock = read_lock(lock_file)
    reports = []
    for name, entry in lock["skills"].items():
        if entry.get("hold"):
            reports.append({"name": name, "status": "held", "reason": entry["hold"]})
            continue
        fetcher = make_fetcher(entry["upstream"], git)
        target = target_commit(fetcher, entry, min_age)
        if target is None:
            reports.append(
                {
                    "name": name,
                    "status": "no upstream commit",
                    "reason": f"nothing at {entry['upstream']}:{entry['path']} old enough",
                }
            )
            continue
        reports.append(skill_report(fetcher, name, entry, target, lock_file.parent / name))
    behind = [r for r in reports if r["status"] == "behind"]
    if as_json:
        print(json.dumps(reports, indent=2))
    else:
        for r in reports:
            if r["status"] == "behind":
                print(
                    f"{r['name']}: behind — {r['upstream'][:7]} ({r['date']}) changes {', '.join(r['changed'])}"
                )
                print(r["diff"], end="")
            elif r["status"] == "current":
                print(
                    f"{r['name']}: current ({(r['pinned'] or 'unpinned')[:7]}, upstream {r['upstream'][:7]} {r['date']})"
                )
            else:
                print(f"{r['name']}: {r['status']} — {r['reason']}")
    return 1 if strict and behind else 0


def apply_local_edits(lock_file: Path, name: str, entry: dict) -> None:
    patch = entry.get("local_edits")
    if not patch:
        return
    # The patch's paths are relative to the lock dir; inside a repo `git apply` reads them from the
    # top level, so point --directory at the lock dir from there.
    lock_dir = lock_file.parent.resolve()
    top = subprocess.run(
        ["git", "-C", str(lock_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    cmd, cwd = ["git", "apply"], lock_dir
    if top.returncode == 0:
        cwd = Path(top.stdout.strip()).resolve()
        rel = lock_dir.relative_to(cwd).as_posix()
        if rel != ".":
            cmd.append(f"--directory={rel}")
    out = subprocess.run(
        [*cmd, str(lock_dir / patch)], cwd=cwd, capture_output=True, text=True, check=False
    )
    if out.returncode:
        sys.exit(
            f"{name}: local_edits patch {patch} does not apply on the new upstream copy:\n{out.stderr.strip()}\nRebase the patch, then rerun `skills-vendor update {name}`."
        )


def update_skill(fetcher, lock_file: Path, name: str, entry: dict, target: Commit) -> dict:
    report = skill_report(fetcher, name, entry, target, lock_file.parent / name)
    skill_dir = lock_file.parent / name
    tree = fetcher.tree(target.sha)
    upstream_files = {}
    for rel in sorted(entry["files"]):
        up = upstream_path(entry, rel)
        if up not in tree:
            sys.exit(
                f"{name}/{rel}: gone from {entry['upstream']}@{target.sha[:7]} ({up}); drop it from the lock or `hold` the skill"
            )
        data = fetcher.blob(target.sha, up)
        (skill_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (skill_dir / rel).write_bytes(data)
        upstream_files[rel] = tree[up]
    apply_local_edits(lock_file, name, entry)
    entry["commit"] = target.sha
    entry["files"] = local_files(skill_dir)
    if entry.get("local_edits"):
        entry["upstream_files"] = upstream_files
    else:
        entry.pop("upstream_files", None)
    return report


def cmd_update(
    lock_file: Path,
    names: list[str],
    all_: bool,
    sha: str | None,
    min_age: int,
    report_file: str | None,
    git: bool,
) -> int:
    lock = read_lock(lock_file)
    if all_:
        names = list(lock["skills"])
    if sha and len(names) != 1:
        sys.exit("--sha pins one skill at a time")
    unknown = [n for n in names if n not in lock["skills"]]
    if unknown:
        sys.exit(f"not in {lock_file}: {', '.join(unknown)}")
    reports = []
    for name in names:
        entry = lock["skills"][name]
        if entry.get("hold"):
            print(f"{name}: held ({entry['hold']}), skipped")
            continue
        fetcher = make_fetcher(entry["upstream"], git)
        target = Commit(sha, datetime.now(UTC)) if sha else target_commit(fetcher, entry, min_age)
        if target is None:
            print(f"{name}: no upstream commit old enough, skipped")
            continue
        if target.sha == entry.get("commit") and not sha:
            print(f"{name}: current at {target.sha[:7]}")
            continue
        r = update_skill(fetcher, lock_file, name, entry, target)
        reports.append(r)
        print(
            f"{name}: {(r['pinned'] or 'unpinned')[:7]} -> {target.sha[:7]} ({len(r['changed'])} file(s) changed)"
        )
    write_lock(lock_file, lock)
    if report_file:
        Path(report_file).write_text(markdown_report(reports), encoding="utf-8")
    return 0


def markdown_report(reports: list[dict], limit: int = 60000) -> str:
    out = []
    for r in reports:
        out.append(
            f"### {r['name']}: {(r['pinned'] or 'unpinned')[:7]} -> {r['upstream'][:7]} ({r['date']})\n"
        )
        if r["diff"]:
            out.append("```diff\n" + r["diff"].rstrip("\n") + "\n```\n")
        else:
            out.append("No change in the vendored files.\n")
    text = "\n".join(out)
    if len(text) > limit:
        text = text[:limit] + "\n```\n\n(truncated; the full diff is the PR's)\n"
    return text


def cmd_init(
    lock_file: Path,
    name: str,
    upstream: str,
    path: str,
    sha: str | None,
    paths: list[str],
    git: bool,
) -> int:
    skill_dir = lock_file.parent / name
    if not skill_dir.is_dir():
        sys.exit(f"{skill_dir}: no such directory")
    lock = read_lock(lock_file) if lock_file.is_file() else {"version": 1, "skills": {}}
    entry = {
        "upstream": upstream,
        "path": path.strip("/"),
        "commit": None,
        "files": local_files(skill_dir),
        "license": detect_license(skill_dir),
        "local_edits": None,
        "hold": None,
    }
    if paths:
        entry["paths"] = dict(p.split("=", 1) for p in paths)
    if not entry["files"]:
        sys.exit(f"{skill_dir}: no files")
    fetcher = make_fetcher(upstream, git)
    if sha:
        entry["commit"] = sha
    else:
        for c in fetcher.commits(entry["path"], 200):
            tree = fetcher.tree(c.sha)
            if all(tree.get(upstream_path(entry, rel)) == s for rel, s in entry["files"].items()):
                entry["commit"] = c.sha
                break
        if not entry["commit"]:
            print(
                f"{name}: no commit of {upstream}:{path} in the last 200 matches the local copy; commit is null (pass --sha, or mark local_edits)",
                file=sys.stderr,
            )
    lock["skills"][name] = entry
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    write_lock(lock_file, lock)
    print(
        f"{name}: {upstream}:{entry['path']} @ {(entry['commit'] or 'null')[:7]}, {len(entry['files'])} file(s), license {entry['license']}"
    )
    return 0


# --- self-test ------------------------------------------------------------------------------------


class FakeFetcher:
    """Commits newest first: (sha, date, {path: bytes})."""

    def __init__(self, history: list[tuple[str, datetime, dict[str, bytes]]]) -> None:
        self.history = history

    def commits(self, path, limit, until=None):
        out = []
        prev: dict[str, bytes] | None = None
        touching = []
        for sha, date, tree in reversed(self.history):
            sub = {p: b for p, b in tree.items() if not path or p.startswith(path + "/")}
            if sub != prev:
                touching.append(Commit(sha, date))
            prev = sub
        for c in reversed(touching):
            if until is None or c.date <= until:
                out.append(c)
        return out[:limit]

    def tree(self, sha):
        return {p: blob_sha(b) for s, _, t in self.history if s == sha for p, b in t.items()}

    def blob(self, sha, path):
        return next(t[path] for s, _, t in self.history if s == sha)


def self_test() -> int:
    import contextlib
    import io

    now = datetime.now(UTC)
    lic = b"MIT License\n\nCopyright (c) 2026 Someone\n"
    v1 = {"skills/x/SKILL.md": b"---\nname: x\n---\nold\n", "LICENSE": lic}
    v2 = {"skills/x/SKILL.md": b"---\nname: x\n---\nnew\n", "LICENSE": lic}
    history = [
        ("b" * 40, now - timedelta(days=2), v2),
        ("a" * 40, now - timedelta(days=30), v1),
        ("0" * 40, now - timedelta(days=60), {"README.md": b"hi\n", "LICENSE": lic}),
    ]
    fake = FakeFetcher(history)
    global make_fetcher
    real = make_fetcher
    make_fetcher = lambda repo, git: fake  # noqa: E731
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_file = root / "skills.lock"
            (root / "x").mkdir()
            (root / "x" / "SKILL.md").write_bytes(v1["skills/x/SKILL.md"])
            (root / "x" / "LICENSE").write_bytes(lic)

            assert cmd_check(lock_file) == 0, "no lock is a no-op"
            assert (
                cmd_init(lock_file, "x", "o/r", "skills/x", None, ["LICENSE=LICENSE"], False) == 0
            )
            lock = read_lock(lock_file)
            e = lock["skills"]["x"]
            assert (
                e["commit"] == "a" * 40
                and e["license"] == "MIT"
                and set(e["files"]) == {"SKILL.md", "LICENSE"}
            ), e
            assert cmd_check(lock_file) == 0

            (root / "x" / "SKILL.md").write_bytes(b"edited\n")
            (root / "x" / "extra").write_bytes(b"?")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                assert cmd_check(lock_file) == 1
            assert (
                "x/SKILL.md: changed" in err.getvalue()
                and "x/extra: not in the lock" in err.getvalue()
            ), err.getvalue()
            (root / "x" / "extra").unlink()
            (root / "x" / "SKILL.md").write_bytes(v1["skills/x/SKILL.md"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert cmd_outdated(lock_file, 0, True, True, False) == 1
            rep = json.loads(out.getvalue())[0]
            assert (
                rep["status"] == "behind"
                and rep["changed"] == ["SKILL.md"]
                and "+new" in rep["diff"]
            ), rep
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert cmd_outdated(lock_file, 14, True, True, False) == 0, (
                    "a 2-day-old commit is too young for --min-age 14"
                )
            assert json.loads(out.getvalue())[0]["status"] == "current"

            with contextlib.redirect_stdout(io.StringIO()):
                assert cmd_update(lock_file, [], True, None, 14, None, False) == 0
            assert read_lock(lock_file)["skills"]["x"]["commit"] == "a" * 40
            report = root / "report.md"
            with contextlib.redirect_stdout(io.StringIO()):
                assert cmd_update(lock_file, ["x"], False, None, 0, str(report), False) == 0
            e = read_lock(lock_file)["skills"]["x"]
            assert (
                e["commit"] == "b" * 40
                and (root / "x" / "SKILL.md").read_bytes() == v2["skills/x/SKILL.md"]
            )
            assert (
                "### x: aaaaaaa -> bbbbbbb" in report.read_text() and "+new" in report.read_text()
            )
            assert cmd_check(lock_file) == 0

            # local_edits: the lock holds the edited shas, upstream_files the pristine ones.
            patch = "--- a/x/SKILL.md\n+++ b/x/SKILL.md\n@@ -1,4 +1,4 @@\n ---\n name: x\n ---\n-new\n+new, but ours\n"
            (root / "x.patch").write_text(patch)
            lock = read_lock(lock_file)
            lock["skills"]["x"]["local_edits"] = "x.patch"
            write_lock(lock_file, lock)
            with contextlib.redirect_stdout(io.StringIO()):
                assert cmd_update(lock_file, ["x"], False, "b" * 40, 0, None, False) == 0
            e = read_lock(lock_file)["skills"]["x"]
            assert (root / "x" / "SKILL.md").read_text().endswith("new, but ours\n")
            assert (
                e["upstream_files"]["SKILL.md"]
                == blob_sha(v2["skills/x/SKILL.md"])
                != e["files"]["SKILL.md"]
            )
            assert cmd_check(lock_file) == 0
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert cmd_outdated(lock_file, 0, True, True, False) == 0, (
                    "edited copy at upstream HEAD is current"
                )
            assert json.loads(out.getvalue())[0]["status"] == "current"

            lock = read_lock(lock_file)
            lock["skills"]["x"]["hold"] = "waiting on licence check"
            write_lock(lock_file, lock)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert cmd_outdated(lock_file, 0, True, True, False) == 0
            assert json.loads(out.getvalue())[0]["status"] == "held"

            (root / "x.patch").write_text(
                "--- a/x/SKILL.md\n+++ b/x/SKILL.md\n@@ -1 +1 @@\n-nope\n+never\n"
            )
            lock["skills"]["x"]["hold"] = None
            write_lock(lock_file, lock)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    cmd_update(lock_file, ["x"], False, "a" * 40, 0, None, False)
                raise AssertionError("a patch that does not apply must fail")
            except SystemExit as exc:
                assert "does not apply" in str(exc), exc
    finally:
        make_fetcher = real
    assert blob_sha(b"hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"
    print("skills-vendor self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--self-test", action="store_true")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lock", help=f"the lock file (default: <repo>/{DEFAULT_LOCK})")
    common.add_argument(
        "--git",
        action="store_true",
        help="read the upstream through a blobless clone instead of the REST API",
    )
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("check", parents=[common], help="offline: every vendored file matches the lock")
    p = sub.add_parser(
        "outdated", parents=[common], help="what upstream changed since the pin (advisory)"
    )
    p.add_argument(
        "--min-age",
        type=int,
        default=0,
        metavar="DAYS",
        help="ignore upstream commits younger than this",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="exit 1 when a skill is behind")
    p = sub.add_parser(
        "update",
        parents=[common],
        help="fetch upstream, rewrite the skill, re-apply local_edits, rewrite the lock",
    )
    p.add_argument("names", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--sha", help="pin this commit (one skill)")
    p.add_argument("--min-age", type=int, default=0, metavar="DAYS")
    p.add_argument("--report", metavar="FILE", help="write a Markdown summary with the diffs")
    p = sub.add_parser(
        "init", parents=[common], help="create a lock entry from the copy already in the tree"
    )
    p.add_argument("name")
    p.add_argument("--upstream", required=True, metavar="OWNER/REPO")
    p.add_argument(
        "--path",
        required=True,
        metavar="DIR",
        help="the skill's directory upstream ('' for the root)",
    )
    p.add_argument("--sha", help="the upstream commit; else the newest of 200 whose tree matches")
    p.add_argument(
        "--paths",
        action="append",
        default=[],
        metavar="FILE=UPSTREAM",
        help="a vendored file that lives outside --path upstream (LICENSE=LICENSE)",
    )
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.cmd:
        ap.print_help()
        return 2
    lock_file = lock_path(a.lock)
    if a.cmd == "check":
        return cmd_check(lock_file)
    if a.cmd == "outdated":
        return cmd_outdated(lock_file, a.min_age, a.json, a.strict, a.git)
    if a.cmd == "update":
        if not a.names and not a.all:
            ap.error("update needs <name>... or --all")
        return cmd_update(lock_file, a.names, a.all, a.sha, a.min_age, a.report, a.git)
    if a.cmd == "init":
        return cmd_init(lock_file, a.name, a.upstream, a.path, a.sha, a.paths, a.git)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
