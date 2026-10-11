#!/usr/bin/env bash
# trailer-fill — add a missing `Tested:` trailer (a `ci-only` placeholder) to every commit of the
# current branch, and a `Closes #N` line to the newest commit's body for a mip-NNNN/k-* branch whose
# tasks row links an issue (#524: a PR-body-only line never closed one). Each commit is replayed
# with `git cherry-pick` onto a scratch branch and only the ones that need it are amended, with
# their dates preserved. The Closes line is body text, not a trailer, since AGENTS.md caps commits
# at two trailers (scripts/lib/task_issue.sh). TASK_PARTIAL=1 opts out of it before the PR's
# `task-partial` label can exist. --base <ref> (default origin/main) bounds the rewrite;
# scripts/stack.sh pr passes a stacked task's parent. --dry-run prints and changes nothing.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/task_issue.sh.
source "$script_dir/lib/task_issue.sh"

self_test() {
  local failed=0 tmp repo self tasks out out2 out3 body2 body3 trailers_out full closes_ln tested_ln t1 fork rc
  check() { if [ "$2" = "$3" ]; then echo "ok: $1"; else echo "FAILED: $1"; echo "  got:  $2"; echo "  want: $3"; failed=1; fi; }
  has()   { case "$2" in *"$3"*) echo "ok: $1" ;; *) echo "FAILED: $1 — expected \"$3\" in:"; sed 's/^/  /' <<<"$2"; failed=1 ;; esac; }
  lacks() { case "$2" in *"$3"*) echo "FAILED: $1 — did not expect \"$3\" in:"; sed 's/^/  /' <<<"$2"; failed=1 ;; *) echo "ok: $1" ;; esac; }

  self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  tmp="$(mktemp -d)"; trap "rm -rf $(printf %q "$tmp")" EXIT
  tasks='| # | slug | delivers | tests | depends on |
|---|---|---|---|---|
| [2](https://github.com/marola-dev/marola/issues/502) | ascii | x | y | 1 |'

  mk_repo() {   # a throwaway repo, one mip task commit ahead of origin/main, into $repo
    repo="$tmp/repo-$RANDOM"
    { git init -q -b main "$repo"
      git -C "$repo" config user.email trailer-fill@example.invalid
      git -C "$repo" config user.name trailer-fill-self-test
      git -C "$repo" commit -q --allow-empty -m main
      # .invalid never resolves (RFC 2606) — trailer-fill's own `git fetch origin main` must fail
      # offline-safe here rather than silently pulling the real marola-dev/marola history.
      git -C "$repo" remote add origin https://github.invalid/marola-dev/marola.git
      git -C "$repo" update-ref refs/remotes/origin/main "$(git -C "$repo" rev-parse HEAD)"
      git -C "$repo" checkout -q -b mip-9999/2-ascii
      mkdir -p "$repo/docs/MIPs"; printf '%s\n' "$tasks" >"$repo/docs/MIPs/MIP-9999.tasks.md"
      git -C "$repo" add docs
      git -C "$repo" commit -q -m $'MIP-9999 task 2: ascii\n\nBody paragraph.\n\nTested: ci-only\nCo-Authored-By: Claude <noreply@anthropic.com>'
    } >/dev/null 2>&1
  }

  echo "-- a task branch's commit gains its row's Closes line above the trailers --"
  mk_repo
  out="$(cd "$repo" && bash "$self" 2>&1)"
  full="$(git -C "$repo" log -1 --format=%B mip-9999/2-ascii)"
  has "trailer-fill reports one Closes line added" "$out" "1 Closes line(s)"
  has "and a per-commit line names the issue and the opt-out" "$out" \
    "trailer-fill: added Closes #502 to"
  has "mentioning TASK_PARTIAL, so it can't close by surprise" "$out" "(TASK_PARTIAL=1 to skip)"
  check "the commit body gains Closes #502" "$(grep -cx 'Closes #502' <<<"$full")" "1"
  trailers_out="$(git -C "$repo" log -1 --format='%(trailers:only,unfold)' mip-9999/2-ascii)"
  check "the two trailers still parse, in order" "$(cut -d: -f1 <<<"$trailers_out" | paste -sd, -)" \
    "Tested,Co-Authored-By"
  closes_ln="$(grep -nx 'Closes #502' <<<"$full" | head -1 | cut -d: -f1)"
  tested_ln="$(grep -n '^Tested:' <<<"$full" | head -1 | cut -d: -f1)"
  check "Closes sits above the trailer block, not inside it" \
    "$([ -n "$closes_ln" ] && [ -n "$tested_ln" ] && [ "$closes_ln" -lt "$tested_ln" ] && echo yes || echo no)" "yes"

  echo
  echo "-- a commit that already has it is not doubled --"
  out2="$(cd "$repo" && bash "$self" 2>&1)"
  body2="$(git -C "$repo" log -1 --format=%B mip-9999/2-ascii)"
  check "re-running trailer-fill adds no second Closes line" "$(grep -cx 'Closes #502' <<<"$body2")" "1"
  has "and it reports nothing left to add" "$out2" "nothing to add"

  echo
  echo "-- the partial opt-out writes none --"
  mk_repo
  out3="$(cd "$repo" && TASK_PARTIAL=1 bash "$self" 2>&1)"
  body3="$(git -C "$repo" log -1 --format=%B mip-9999/2-ascii)"
  check "no Closes line is written" "$(grep -c '^Closes #' <<<"$body3")" "0"
  lacks "and trailer-fill never mentions Closes" "$out3" "Closes #"

  # Task 1 already pushed with its own Closes but no Tested: trailer: a range from origin/main
  # would both see #501 and rewrite task 1's commit.
  mk_stack() {
    repo="$tmp/stack-$RANDOM"
    { git init -q -b main "$repo"
      git -C "$repo" config user.email trailer-fill@example.invalid
      git -C "$repo" config user.name trailer-fill-self-test
      git -C "$repo" commit -q --allow-empty -m main
      git -C "$repo" remote add origin https://github.invalid/marola-dev/marola.git
      git -C "$repo" update-ref refs/remotes/origin/main "$(git -C "$repo" rev-parse HEAD)"
      git -C "$repo" checkout -q -b mip-9999/1-a
      mkdir -p "$repo/docs/MIPs"
      printf '%s\n' '| # | slug | delivers | tests | depends on |' '|---|---|---|---|---|' \
        '| [1](https://github.com/marola-dev/marola/issues/501) | a | x | y | |' \
        '| [2](https://github.com/marola-dev/marola/issues/502) | b | x | y | 1 |' >"$repo/docs/MIPs/MIP-9999.tasks.md"
      git -C "$repo" add docs
      git -C "$repo" commit -q -m $'task 1\n\nCloses #501\n\nCo-Authored-By: Claude <noreply@anthropic.com>'
      git -C "$repo" update-ref refs/remotes/origin/mip-9999/1-a "$(git -C "$repo" rev-parse HEAD)"
      git -C "$repo" checkout -q -b mip-9999/2-b
      git -C "$repo" commit -q --allow-empty -m $'task 2\n\nTested: ci-only\nCo-Authored-By: Claude <noreply@anthropic.com>'
    } >/dev/null 2>&1
  }

  echo
  echo "-- task 2 stacked on a pushed task 1 gains its own Closes line --"
  mk_stack
  t1="$(git -C "$repo" rev-parse mip-9999/1-a)"
  out="$(cd "$repo" && bash "$self" --base origin/mip-9999/1-a 2>&1)" || true
  full="$(git -C "$repo" log -1 --format=%B mip-9999/2-b)"
  check "task 2's commit carries Closes #502" "$(grep -cx 'Closes #502' <<<"$full")" "1"
  check "and not task 1's #501" "$(grep -cx 'Closes #501' <<<"$full")" "0"

  echo
  echo "-- filling task 2 leaves task 1's commits untouched --"
  check "task 1's branch is still an ancestor of task 2" \
    "$(git -C "$repo" merge-base --is-ancestor mip-9999/1-a mip-9999/2-b && echo yes || echo no)" "yes"
  check "task 1's commit is task 2's parent, unchanged" "$(git -C "$repo" rev-parse mip-9999/2-b~1)" "$t1"

  echo
  echo "-- trailer-fill does not refuse when origin/main advanced by an unrelated commit --"
  mk_repo
  fork="$(git -C "$repo" rev-parse origin/main)"
  { git -C "$repo" checkout -q main
    git -C "$repo" commit -q --allow-empty -m "someone else merged"
    git -C "$repo" update-ref refs/remotes/origin/main "$(git -C "$repo" rev-parse HEAD)"
    git -C "$repo" checkout -q mip-9999/2-ascii; } >/dev/null 2>&1
  rc=0; out="$(cd "$repo" && bash "$self" --dry-run 2>&1)" || rc=$?
  check "--dry-run succeeds" "$rc" "0"
  has "and previews the rewrite" "$out" "would rewrite"
  rc=0; out="$(cd "$repo" && bash "$self" 2>&1)" || rc=$?
  check "the real run succeeds too" "$rc" "0"
  check "the rewritten commit sits on the same base as before" "$(git -C "$repo" rev-parse mip-9999/2-ascii~1)" "$fork"
  check "and gained its Closes line" "$(git -C "$repo" log -1 --format=%B mip-9999/2-ascii | grep -cx 'Closes #502')" "1"

  echo
  echo "-- a commit missing Tested: gains the placeholder and no other trailer --"
  mk_repo
  { git -C "$repo" commit -q --allow-empty -m $'no trailers yet\n\nCo-Authored-By: Claude <noreply@anthropic.com>'; } >/dev/null 2>&1
  out="$(cd "$repo" && TASK_PARTIAL=1 bash "$self" 2>&1)" || true
  full="$(git -C "$repo" log -1 --format=%B mip-9999/2-ascii)"
  has "the Tested: placeholder is added" "$full" "Tested: ci-only"
  trailers_out="$(git -C "$repo" log -1 --format='%(trailers:only,unfold)' mip-9999/2-ascii)"
  check "the trailers are Tested then Co-Authored-By" "$(cut -d: -f1 <<<"$trailers_out" | paste -sd, -)" \
    "Tested,Co-Authored-By"

  echo
  if [ "$failed" -eq 1 ]; then echo "trailer-fill self-test: FAILED" >&2; return 1; fi
  echo "trailer-fill self-test: ok"
}

dry_run=0; base=origin/main
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test; exit $? ;;
    --dry-run) dry_run=1 ;;
    --base) base="${2:?--base needs a ref}"; shift ;;
  esac
  shift
done

branch="$(git branch --show-current)"
[ -n "$branch" ] || { echo "trailer-fill: detached HEAD — check out a branch first" >&2; exit 1; }
[ "$branch" != main ] || { echo "trailer-fill: refusing to rewrite main" >&2; exit 1; }
git fetch -q origin main 2>/dev/null || true
# --base: a stacked task's parent branch (scripts/stack.sh pr), so the range, the Closes dedupe and
# the replay point cover only this task's own commits. Replaying onto the merge-base, not the
# base's tip, means an unrelated advance of that base needs no rebase first.
fork="$(git merge-base "$base" HEAD)" || { echo "trailer-fill: $branch shares no history with $base" >&2; exit 1; }

mapfile -t shas < <(git log --reverse --format=%H "$fork..HEAD")
if [ "${#shas[@]}" -eq 0 ]; then
  echo "trailer-fill: no commits ahead of $base on $branch"
  exit 0
fi

TESTED_FALLBACK="Tested: ci-only — trailer added by just trailer-fill, gates not re-run"

declare -A new_tested new_closes
any=0
for sha in "${shas[@]}"; do
  if ! git log -1 --format=%B "$sha" | grep -q '^Tested:'; then
    new_tested["$sha"]="$TESTED_FALLBACK"
    any=1
  fi
done

# One Closes line per branch, on its newest commit — not per commit, so a multi-commit task branch
# doesn't scatter the same keyword across several commit bodies. TASK_PARTIAL is the opt-out: the
# PR's own task-partial label can't exist yet at first push, so this can't key off it.
if [ "${TASK_PARTIAL:-}" != 1 ] && [[ "$branch" =~ ^(mip-[0-9]{4})/[0-9]+- ]]; then
  mip="$(tr '[:lower:]' '[:upper:]' <<<"${BASH_REMATCH[1]}")"
  tasks="$(git show "$branch:docs/MIPs/$mip.tasks.md" 2>/dev/null)" || tasks=""
  if [ -n "$tasks" ]; then
    repo_slug="$(git remote get-url origin 2>/dev/null \
      | sed -E 's#^git@([^:]+):#https://\1/#; s#^ssh://git@#https://#; s#\.git$##; s#^https://[^/]+/##')"
    closes_line="$(task_issue_line "$branch" "$tasks" "" "$repo_slug")"
    if [ -n "$closes_line" ]; then
      already=0
      for sha in "${shas[@]}"; do
        if git log -1 --format=%B "$sha" | grep -qE "^(Closes|Fixes|Resolves|Part of) #${closes_line##*#}\$"; then
          already=1; break
        fi
      done
      if [ "$already" -eq 0 ]; then
        new_closes["${shas[-1]}"]="$closes_line"
        any=1
      fi
    fi
  fi
fi

if [ "$any" -eq 0 ]; then
  echo "trailer-fill: nothing to add on $branch — trailers and any Closes line are already there"
  exit 0
fi

if [ "$dry_run" -eq 1 ]; then
  echo "trailer-fill --dry-run: would rewrite $branch, replaying ${#shas[@]} commit(s) from ${fork:0:7} and amending these (dates preserved):"
  for sha in "${shas[@]}"; do
    [ -n "${new_tested[$sha]:-}${new_closes[$sha]:-}" ] || continue
    echo "  $(git log -1 --format='%h %s' "$sha")"
    [ -n "${new_tested[$sha]:-}" ] && echo "    + ${new_tested[$sha]}"
    [ -n "${new_closes[$sha]:-}" ] && echo "    + ${new_closes[$sha]}"
  done
  exit 0
fi

# Tested goes first, ahead of whatever trailers were already there; the Closes line goes in the
# body so `%(trailers:only,unfold)` still prints only the trailers.
insert_trailers() {   # $1 = old message, $2 = new Tested line or "", $3 = new Closes line or ""
  python3 - "$1" "$2" "$3" <<'PY'
import re
import sys

msg, tested_new, closes_new = sys.argv[1], sys.argv[2], sys.argv[3]
lines = msg.rstrip("\n").split("\n")
trailer_re = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")
i = len(lines)
while i > 0 and trailer_re.match(lines[i - 1]):
    i -= 1
existing = lines[i:]
body = lines[:i]
while body and body[-1].strip() == "":
    body.pop()

if closes_new:
    body.append("")
    body.append(closes_new)

tested_line = tested_new or next((line for line in existing if line.startswith("Tested:")), None)
others = [line for line in existing if not line.startswith("Tested:")]
ordered = ([tested_line] if tested_line else []) + others

sys.stdout.write("\n".join(body) + "\n\n" + "\n".join(ordered) + "\n")
PY
}

tmp_branch="trailer-fill-tmp-$$"
git branch -f "$tmp_branch" "$fork" >/dev/null
git checkout -q "$tmp_branch"
# Whatever happens mid-replay (a conflict, Ctrl-C), leave the caller on their own branch with a
# clean tree, not on the temp branch in a half-done cherry-pick.
trap 'git cherry-pick --abort >/dev/null 2>&1 || true; git checkout -q -f "$branch" 2>/dev/null || git checkout -q "$branch" 2>/dev/null; git branch -D "$tmp_branch" >/dev/null 2>&1 || true' EXIT

for sha in "${shas[@]}"; do
  cdate="$(git log -1 --format=%cI "$sha")"
  # Replaying onto the commits' own fork point, so nothing can turn empty; --allow-empty keeps
  # one that was authored empty.
  GIT_COMMITTER_DATE="$cdate" git cherry-pick --allow-empty "$sha" >/dev/null
  tested="${new_tested[$sha]:-}"
  closes="${new_closes[$sha]:-}"
  if [ -n "$tested$closes" ]; then
    old_msg="$(git log -1 --format=%B)"
    new_msg="$(insert_trailers "$old_msg" "$tested" "$closes")"
    GIT_COMMITTER_DATE="$cdate" git commit -q --amend --allow-empty -m "$new_msg"
    [ -z "$closes" ] || echo "trailer-fill: added $closes to $(git rev-parse --short HEAD) (TASK_PARTIAL=1 to skip)"
  fi
done
git branch -f "$branch" "$tmp_branch"
trap - EXIT
git checkout -q "$branch"
git branch -D "$tmp_branch" >/dev/null

# `${#assoc[@]}` on an empty associative array trips `set -u` on bash 5.3, so count without it.
n_tested=0; n_closes=0
for _ in "${!new_tested[@]}"; do n_tested=$((n_tested + 1)); done 2>/dev/null || true
for _ in "${!new_closes[@]}"; do n_closes=$((n_closes + 1)); done 2>/dev/null || true
echo "trailer-fill: added $n_tested Tested: trailer(s) and $n_closes Closes line(s) across ${#shas[@]} commit(s) on $branch"
echo "next: git push --force-with-lease origin $branch"
