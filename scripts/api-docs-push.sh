#!/usr/bin/env bash
# api-docs-push — force-push a directory's contents as one orphan commit to a branch (MIP-0074
# §5.2: api-docs.yml's push step calls this after `just api-docs <out>` runs). Only the latest
# output is kept, so the branch never grows; the source sha goes in the commit message so a
# consumer (fetch-api-docs) can tell what it pulled.
#
#   api-docs-push <dir> <remote> <sha> [--branch api-docs]
#   api-docs-push --self-test
#
# <remote> is anything `git push` accepts (a URL or a local path) — the caller embeds any
# credentials into it (e.g. https://x-access-token:$GITHUB_TOKEN@github.com/owner/repo.git);
# this script never reads GITHUB_TOKEN itself, so its self-test needs no network, just a local
# bare repo.
set -euo pipefail

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# push <dir> <remote> <sha> <branch> — build the orphan commit in a scratch worktree so the
# caller's own checkout is never touched, then force-push it. The whole git sequence runs in a
# subshell with GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_PREFIX/GIT_COMMON_DIR unset: `-C "$tmp"`
# only chdir's, it does not override an inherited GIT_DIR, so without this a caller running under
# one (a git hook, e.g. this repo's own prepush) would have redirected every git call below onto
# its *own* repo instead of the scratch one — reproduced against a decoy repo before this fix.
push() {
  local dir=$1 remote=$2 sha=$3 branch=$4 tmp
  [ -d "$dir" ] || { echo "api-docs-push: $dir: not a directory" >&2; return 1; }
  [ -n "$(ls -A "$dir" 2>/dev/null)" ] || { echo "api-docs-push: $dir is empty" >&2; return 1; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  (
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
    git -C "$tmp" init -q
    git -C "$tmp" symbolic-ref HEAD "refs/heads/$branch"
    cp -a "$dir"/. "$tmp"/
    git -C "$tmp" -c user.name=api-docs-push -c user.email=api-docs-push@marola.dev \
      -c commit.gpgsign=false -c core.hooksPath=/dev/null add -A
    git -C "$tmp" -c user.name=api-docs-push -c user.email=api-docs-push@marola.dev \
      -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -q -m "api-docs: $sha"
    git -C "$tmp" push -q --force "$remote" "$branch:$branch"
  )
}

self_test() {
  local tmp fails=0 out bare content clone decoy decoy_head decoy_log
  # Unset for the self-test's own git calls too (not just push()'s), in case --self-test itself
  # is invoked under an inherited GIT_DIR (tests/self-tests.sh under this repo's own prepush hook).
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
  check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else fails=$((fails + 1)); echo "  FAIL $1 — got [$2] want [$3]" >&2; fi; }
  has() { case "$2" in *"$3"*) echo "  ok   $1" ;; *) fails=$((fails + 1)); echo "  FAIL $1 — expected \"$3\" in: $2" >&2 ;; esac; }
  hasnot() { case "$2" in *"$3"*) fails=$((fails + 1)); echo "  FAIL $1 — did not expect \"$3\" in: $2" >&2 ;; *) echo "  ok   $1" ;; esac; }

  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN

  bare="$tmp/api-docs-host.git"
  git init -q --bare "$bare"

  content="$tmp/content"; mkdir -p "$content"
  echo first >"$content/index.html"
  push "$content" "$bare" sha1111 api-docs

  clone="$tmp/clone"
  git clone -q "$bare" "$clone" 2>/dev/null
  git -C "$clone" checkout -q -b api-docs origin/api-docs
  check "first push: exactly one commit" "$(git -C "$clone" rev-list --count api-docs)" "1"
  has "first push: message names the sha" "$(git -C "$clone" log -1 --format=%s api-docs)" "sha1111"
  check "first push: content landed" "$(cat "$clone/index.html")" "first"

  echo second >"$content/index.html"
  push "$content" "$bare" sha2222 api-docs

  git -C "$clone" fetch -q origin api-docs
  git -C "$clone" checkout -q -B api-docs origin/api-docs
  check "second push: still exactly one commit" "$(git -C "$clone" rev-list --count api-docs)" "1"
  out="$(git -C "$clone" log -1 --format=%s api-docs)"
  has "second push: message names the new sha" "$out" "sha2222"
  hasnot "second push: the old sha is gone" "$out" "sha1111"
  check "second push: content replaced" "$(cat "$clone/index.html")" "second"

  echo "-- an inherited GIT_DIR (e.g. a caller's git hook) must not leak into push() --"
  decoy="$tmp/decoy"
  git init -q "$decoy"
  git -C "$decoy" -c user.name=t -c user.email=t@example.com commit -q --allow-empty -m "decoy initial"
  decoy_head="$(git -C "$decoy" symbolic-ref HEAD)"
  decoy_log="$(git -C "$decoy" log --oneline)"
  GIT_DIR="$decoy/.git" push "$content" "$bare" sha3333 api-docs
  check "GIT_DIR leak: decoy HEAD unchanged" "$(git -C "$decoy" symbolic-ref HEAD)" "$decoy_head"
  check "GIT_DIR leak: decoy log unchanged" "$(git -C "$decoy" log --oneline)" "$decoy_log"
  git -C "$clone" fetch -q origin api-docs
  git -C "$clone" checkout -q -B api-docs origin/api-docs
  has "GIT_DIR leak: the push still reached the real remote" "$(git -C "$clone" log -1 --format=%s api-docs)" "sha3333"

  if [ "$fails" -eq 0 ]; then echo "api-docs-push self-test: ok"; return 0; fi
  echo "api-docs-push self-test: $fails failure(s)" >&2; return 1
}

branch=api-docs
self_test_flag=0
args=()

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) branch="${2:?--branch needs a name}"; shift 2 ;;
    --self-test) self_test_flag=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "api-docs-push: unknown argument: $1" >&2; usage >&2; exit 1 ;;
    *) args+=("$1"); shift ;;
  esac
done

if [ "$self_test_flag" -eq 1 ]; then self_test; exit $?; fi

[ "${#args[@]}" -eq 3 ] || { echo "api-docs-push: expects <dir> <remote> <sha>" >&2; usage >&2; exit 1; }
push "${args[0]}" "${args[1]}" "${args[2]}" "$branch"
