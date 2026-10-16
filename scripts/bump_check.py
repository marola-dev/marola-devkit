#!/usr/bin/env python3
"""bump-check: is HEAD an allowed devkit bump of BASE? (bump-consumers publish, devkit#77)

bump_check.py DIR BASE HEAD X.Y.Z TAG_REV   # exit 0 allowed; 1 refused, one `rule: detail` line

DIR is a git checkout holding both commits; TAG_REV is the commit vX.Y.Z points at. HEAD's tree
may differ from `release.py --consumer` on BASE's tree only in flake.lock's marola-devkit node and
in REPOS.md's wiring block.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import release

LOCK = "flake.lock"
REPOS_MD = "docs/2-Building-marola/REPOS.md"
START, END = "<!-- wiring:start -->", "<!-- wiring:end -->"
DEVKIT = "marola-devkit"
MOVES = ("rev", "narHash", "lastModified")  # what `nix flake update` rewrites in `locked`


def git(d, *args, env=None, data=None):
    r = subprocess.run(
        ["git", "-C", str(d), *args], capture_output=True, env=env, input=data, check=False
    )
    if r.returncode:
        raise RuntimeError(f"git {args[0]}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def blob(d, rev, path):
    """The file's bytes at rev, or None when rev has no such file."""
    try:
        return git(d, "cat-file", "blob", f"{rev}:{path}")
    except RuntimeError:
        return None


def mode(d, rev, path):
    out = git(d, "ls-tree", rev, "--", path).split()
    return out[0] if out else None


def expected_tree(d, base, version):
    """BASE's tree with release.consumer applied, modes and gitlinks kept exact."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "src"
        src.mkdir()
        tar = git(d, "archive", base)
        subprocess.run(["tar", "-x", "-C", str(src)], input=tar, check=True)
        changed = release.consumer(src, version)
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp / "index")}
        git(d, "read-tree", base, env=env)
        for rel in changed:
            m = mode(d, base, rel).decode()
            sha = git(d, "hash-object", "-w", str(src / rel)).decode().strip()
            git(d, "update-index", "--cacheinfo", f"{m},{sha},{rel}", env=env)
        return git(d, "write-tree", env=env).decode().strip()


def _pairs(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _refuse_constant(c):
    raise ValueError(c)


def load(raw):
    """Strict JSON: nix's parser and Python's must agree on what the file says."""
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_refuse_constant)


def _locked_rule(bd, hd, version, tag_rev):
    bl, hl = bd["locked"], hd["locked"]
    if set(bl) != set(hl):
        return f"flake-lock: {DEVKIT} locked keys changed"
    for k in bl:
        if k not in MOVES and bl[k] != hl[k]:
            return f"flake-lock: {DEVKIT} locked.{k} changed"
    if hl.get("rev") != tag_rev:
        return f"flake-lock: {DEVKIT} locked.rev is not v{version}"
    if hd["original"] != {**bd["original"], "ref": f"v{version}"}:
        return f"flake-lock: {DEVKIT} original is not the old one at v{version}"
    return None


def lock_rule(d, base, head, version, tag_rev):
    b, h = blob(d, base, LOCK), blob(d, head, LOCK)
    if b is None or h is None:
        return f"flake-lock: {LOCK} is {'added' if b is None else 'deleted'}"
    if mode(d, base, LOCK) != mode(d, head, LOCK):
        return f"flake-lock: {LOCK} file mode changed"
    try:
        bj, hj = load(b), load(h)
        bn, hn = bj["nodes"], hj["nodes"]
        if not (isinstance(bn, dict) and isinstance(hn, dict)):
            raise TypeError
        if not all(isinstance(n, dict) for n in (*bn.values(), *hn.values())):
            raise TypeError
        if {k: v for k, v in bj.items() if k != "nodes"} != {
            k: v for k, v in hj.items() if k != "nodes"
        }:
            return "flake-lock: a top-level key other than nodes changed"
        if set(bn) != set(hn):
            return f"flake-lock: nodes added or removed: {sorted(set(bn) ^ set(hn))}"
        # A devkit release that changes the devkit's own flake.lock also moves its non-follows
        # transitive nodes; this refuses them and a person bumps that repo by hand.
        for name in sorted(bn):
            if name != DEVKIT and bn[name] != hn[name]:
                return f"flake-lock: node {name} changed"
        if DEVKIT not in bn:
            return None
        bd, hd = bn[DEVKIT], hn[DEVKIT]
        if not all(isinstance(n[k], dict) for n in (bd, hd) for k in ("locked", "original")):
            raise TypeError

        def rest(n):
            return {k: v for k, v in n.items() if k not in ("locked", "original")}

        if rest(bd) != rest(hd):
            return f"flake-lock: {DEVKIT} changed outside locked and original"
        return _locked_rule(bd, hd, version, tag_rev)
    except (ValueError, KeyError, TypeError, RecursionError):
        return f"flake-lock: {LOCK} is not a well-formed lock in both commits"


def split_block(text):
    """(before incl. start marker, between, after from end marker), or None if markers are wrong."""
    lines = text.split("\n")
    if lines.count(START) != 1 or lines.count(END) != 1:
        return None
    i, j = lines.index(START), lines.index(END)
    return (lines[: i + 1], lines[i + 1 : j], lines[j:]) if i < j else None


def repos_rule(d, base, head):
    b, h = blob(d, base, REPOS_MD), blob(d, head, REPOS_MD)
    if b is None or h is None:
        return f"repos-md: {REPOS_MD} is {'added' if b is None else 'deleted'}"
    if mode(d, base, REPOS_MD) != mode(d, head, REPOS_MD):
        return f"repos-md: {REPOS_MD} file mode changed"
    bt, ht = (x.decode("utf-8", "surrogateescape") for x in (b, h))
    bs, hs = split_block(bt), split_block(ht)
    if bs is None or hs is None:
        return f"repos-md: {REPOS_MD} changed without one {START} ... {END} block in both"
    if bs[0] != hs[0] or bs[2] != hs[2]:
        return f"repos-md: {REPOS_MD} changed outside the wiring block"
    return None


def check(d, base, head, version, tag_rev):
    """None when head is an allowed bump of base, else `<rule>: <detail>`."""
    version = version.removeprefix("v")
    # git am applies every commit of a patch and publish re-authors only the last.
    chain = git(d, "rev-list", "--parents", "-n", "1", head).decode().split()
    full = git(d, "rev-parse", f"{base}^{{commit}}").decode().strip()
    if chain[1:] != [full] or git(d, "rev-list", "--count", f"{base}..{head}").strip() != b"1":
        return f"commits: {head} is not one commit on top of {base}"
    expected = expected_tree(d, base, version)
    diff = git(d, "diff-tree", "-r", "--name-only", "-z", expected, f"{head}^{{tree}}")
    differ = [p for p in diff.decode("utf-8", "surrogateescape").split("\0") if p]
    for p in differ:
        if p not in (LOCK, REPOS_MD):
            return f"outside-pins: {p}"
    if LOCK in differ and (msg := lock_rule(d, base, head, version, tag_rev)):
        return msg
    if REPOS_MD in differ and (msg := repos_rule(d, base, head)):
        return msg
    return None


def main(argv):
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    d, base, head, version, tag_rev = argv
    if not re.fullmatch(rf"v?{release.SEMVER}", version) or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", tag_rev
    ):
        print("bump_check: version must be X.Y.Z and TAG_REV a full commit SHA", file=sys.stderr)
        return 2
    try:
        msg = check(d, base, head, version, tag_rev)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        print(f"bump_check: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # exit 1 means a rule refused the patch, never a crash
        print(f"bump-check: error: {type(e).__name__}: {e}".replace("\n", " "), file=sys.stderr)
        return 2
    if msg:
        print(msg.replace("\n", " "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
