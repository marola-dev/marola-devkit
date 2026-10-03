#!/usr/bin/env bash
# stack — stacked-PR mechanics for MIP task branches (see .claude/skills/mip-tasks/SKILL.md).
# usage: scripts/stack.sh [--dry-run] [--onto-base <sha>] <subcommand> [args]
#   start MIP-0005 1 board-json   branch mip-0005/1-board-json off origin/main
#   start MIP-0005 2 map-page     branch off the previous task branch (mip-0005/1-*)
#   pr                            push + open/update the PR with the right base
#   restack                       after the base PR was squash-merged: rebase onto main
#   status [MIP-0005]             every branch of this MIP with its base and PR
#   branches [MIP-0005]           the MIP's task branches, local or remote, bottom to top
#   link [MIP-0005]               GitHub Stack (gh stack link) from the *open* PRs, bottom to top
#   --dry-run                     print the commands instead of running them
#   --onto-base <sha>             restack's fork point, when it refuses because it found none
#   --self-test                   run this script's own checks
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dry=0; onto_base=""; selftest=0; args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --onto-base) onto_base="${2:?--onto-base needs a commit}"; shift 2 ;;
    --self-test) selftest=1; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- ${args[@]+"${args[@]}"}
run() { if [ "$dry" -eq 1 ]; then echo "+ $*"; else "$@"; fi; }

cur="${STACK_SELFTEST_BRANCH:-$(git branch --show-current)}"   # STACK_SELFTEST_BRANCH: self-test hook
mip_of() { sed -n 's#^\(mip-[0-9]\{4\}\)/.*#\1#p' <<<"$1"; }
task_of() { sed -n 's#^mip-[0-9]\{4\}/\([0-9]*\)-.*#\1#p' <<<"$1"; }
branches_of() {   # every task branch of a MIP, local or on origin, sorted by task number
  { git branch --list "$1/*" --format='%(refname:short)'
    git branch -r --list "origin/$1/*" --format='%(refname:short)' | sed 's#^origin/##'; } | sort -u | sort -t/ -k2 -n
}
mip_arg() {       # the MIP from $1 (MIP-0005 / mip-0005) or from the current branch
  local m; m="$(tr 'A-Z' 'a-z' <<<"${1:-$(mip_of "$cur")}")"
  [ -n "$m" ] || { echo "not on a mip-NNNN/* branch — pass the MIP: MIP-0005" >&2; exit 1; }
  echo "$m"
}
# Task numbers are global to a MIP and its rows land in different repos (MIP-0070), so a repo's
# first row is often not task 1 and its rows skip numbers: the base is the nearest lower one here.
base_for() {   # the nearest lower task branch, local or on origin, else main
  local mip k b bk prev=""; mip="$(mip_of "$1")"; k="$(task_of "$1")"
  [ -n "$k" ] || { echo "not a task branch: $1" >&2; exit 1; }
  while read -r b; do
    bk="$(task_of "$b")"
    if [ -n "$bk" ] && [ "$bk" -lt "$k" ]; then prev="$b"; fi
  done < <(branches_of "$mip")
  printf '%s\n' "${prev:-main}"
}

# `sed 1d` drops uprd's dry-run banner, and nothing else: the body's `Closes #N` (#512) must survive.
new_pr_body() {   # <base>
  echo "Stacked on \`$1\` — part of ${cur%%/*}. Merge order: base first."; echo
  "${STACK_SELFTEST_UPRD:-$script_dir/uprd.sh}" --dry-run 2>/dev/null | sed '1d'
}

pr_window=200
merged_below() {   # head commit of the merged PR of the nearest lower task; it outlives the branch GitHub deletes
  # gh orders by PR number, so .[0] is the newest-*opened* match; the values go via the env, unquotable otherwise.
  MIP="$(mip_of "$1")" K="$(task_of "$1")" gh pr list --state merged --limit "$pr_window" --json headRefName,headRefOid \
    --jq '[.[] | (.headRefName | capture("^(?<m>mip-[0-9]{4})/(?<n>[0-9]+)-")) as $c
           | select($c.m == env.MIP and ($c.n | tonumber) < (env.K | tonumber))
           | {n: ($c.n | tonumber), sha: .headRefOid}]
          | (map(.n) | max) as $top | map(select(.n == $top)) | .[0].sha // empty' 2>/dev/null
}

# Where `rebase --onto` must cut. The trap: a squash-merge rewrites the base's commits (so patch-id
# skipping cannot tell they are already upstream) and GitHub deletes the base branch, so the live base
# base_for finds may sit below the merged parent; with no gh to ask, refuse unless the base is in cur's
# history. Local `main` is never fetched, so origin/main stands in for it.
fork_point_for() {
  local cur="$1" base="$2" k mb="" merged
  k="$(task_of "$cur")"
  if [ "$base" = main ]; then base=origin/main; fi
  if [ -n "$base" ] && git rev-parse --verify -q "$base^{commit}" >/dev/null; then
    mb="$(git merge-base "$cur" "$base")" || mb=""
  fi
  if [ -n "$k" ] && [ "$k" -gt 1 ]; then
    if ! merged="$(merged_below "$cur")"; then
      [ "$base" != origin/main ] && git merge-base --is-ancestor "$base" "$cur" 2>/dev/null || return 0
    elif [ -n "$merged" ] && git merge-base --is-ancestor "$merged" "$cur" 2>/dev/null \
         && { [ -z "$mb" ] || git merge-base --is-ancestor "$mb" "$merged"; }; then
      echo "$merged"; return
    fi
  fi
  [ -z "$mb" ] || echo "$mb"
}

self_test() {
  local failed=0 tmp self repo fork unrelated upstream got out rc
  local pr_origin pr_work cost_fill_stub local_head remote_head
  local fp_origin fp_work upstream_ref rs_origin rs_work main_tip variant t1_head bf
  self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  tmp="$(mktemp -d)"; trap "rm -rf $(printf %q "$tmp")" EXIT
  check() { if [ "$2" = "$3" ]; then echo "ok: $1"; else echo "FAILED: $1"; echo "  got:  $2"; echo "  want: $3"; failed=1; fi; }
  has()   { case "$2" in *"$3"*) echo "ok: $1" ;; *) echo "FAILED: $1 — expected \"$3\" in:"; sed 's/^/  /' <<<"$2"; failed=1 ;; esac; }
  lacks() { case "$2" in *"$3"*) echo "FAILED: $1 — did not expect \"$3\" in:"; sed 's/^/  /' <<<"$2"; failed=1 ;; *) echo "ok: $1" ;; esac; }

  echo "-- branch parsing --"
  check "mip from a task branch" "$(mip_of mip-0063/6-board-and-claim)" "mip-0063"
  check "task number from a task branch" "$(task_of mip-0063/6-board-and-claim)" "6"
  check "a non-task branch yields nothing" "$(mip_of chore/whatever)" ""
  check "task 1 bases on main" "$(base_for mip-0063/1-taxonomy)" "main"

  # A throwaway stack: main <- mip-9999/5-prev (merged, branch deleted) <- mip-9999/6-board. The other
  # branches are traps: 50- collides with the 5 prefix, 8- shares no history, origin/main outruns main.
  repo="$tmp/repo"
  { git init -q -b main "$repo"
    git -C "$repo" config user.email stack@example.invalid
    git -C "$repo" config user.name  stack-self-test
    git -C "$repo" commit -q --allow-empty -m "main"
    git -C "$repo" update-ref refs/remotes/origin/main "$(git -C "$repo" rev-parse HEAD)"
    git -C "$repo" checkout -q -b mip-9999/50-collision
    git -C "$repo" commit -q --allow-empty -m "an unrelated branch"
    unrelated="$(git -C "$repo" rev-parse HEAD)"
    git -C "$repo" checkout -q -b mip-9999/5-prev main
    git -C "$repo" commit -q --allow-empty -m "task 5"
    fork="$(git -C "$repo" rev-parse HEAD)"
    git -C "$repo" checkout -q -b mip-9999/6-board
    git -C "$repo" commit -q --allow-empty -m "task 6"
    git -C "$repo" checkout -q -b mip-9999/1-solo main
    git -C "$repo" commit -q --allow-empty -m "main moved on, and only origin/main knows"
    git -C "$repo" update-ref refs/remotes/origin/main "$(git -C "$repo" rev-parse HEAD)"
    upstream="$(git -C "$repo" rev-parse HEAD)"
    git -C "$repo" commit -q --allow-empty -m "task 1"
    git -C "$repo" checkout -q -b mip-9999/9-cur main
    git -C "$repo" commit -q --allow-empty -m "task 9"
    git -C "$repo" checkout -q --orphan mip-9999/8-unrelated-base
    git -C "$repo" commit -q --allow-empty -m "a base sharing no history"
    git -C "$repo" checkout -q main
    git -C "$repo" branch -D mip-9999/5-prev mip-9999/50-collision; } >/dev/null 2>&1

  mkdir -p "$tmp/bin"
  cat > "$tmp/bin/gh" <<GH
#!$BASH
# a stand-in for \`gh pr list --jq\`: newest PR first; 50- collides on a hyphen-less 5 prefix, 9998 is another MIP.
case "\$*" in
  *"pr list"*)
    while [ \$# -gt 0 ]; do [ "\$1" = --jq ] && { jq -r "\$2" <<'EOF'; exit; }
[{"headRefName":"mip-9999/50-collision","headRefOid":"$unrelated"},
 {"headRefName":"mip-9998/5-other-mip","headRefOid":"$unrelated"},
 {"headRefName":"mip-9999/5-prev","headRefOid":"$fork"}]
EOF
      shift; done
    exit 1 ;;
  *) exit 1 ;;
esac
GH
  chmod +x "$tmp/bin/gh"
  drive_as() { local b="$1"; shift; ( cd "$repo" && PATH="$tmp/bin:$PATH" STACK_SELFTEST_BRANCH="$b" bash "$self" --dry-run restack "$@" 2>&1 ); }
  drive() { drive_as mip-9999/6-board "$@"; }

  echo
  echo "-- base_for: the nearest lower task branch in this repo, else main (#13) --"
  # Task numbers are global to a MIP, so a repo's rows start above 1 and skip other repos' numbers.
  bf="$tmp/bf"
  { git init -q -b main "$bf"
    git -C "$bf" config user.email bf@example.invalid
    git -C "$bf" config user.name bf-self-test
    git -C "$bf" commit -q --allow-empty -m main
    git -C "$bf" branch mip-9999/3-x; } >/dev/null 2>&1
  check "base_for_first_row_in_repo_is_main" "$(cd "$bf" && base_for mip-9999/3-x)" "main"
  { git -C "$bf" branch mip-9999/5-a
    git -C "$bf" branch mip-9999/7-b
    git -C "$bf" branch mip-9999/50-c
    git -C "$bf" branch mip-9998/6-other-mip; } >/dev/null 2>&1
  check "base_for_skips_missing_numbers" "$(cd "$bf" && base_for mip-9999/7-b)" "mip-9999/5-a"
  git -C "$bf" update-ref refs/remotes/origin/mip-9999/6-remote-only "$(git -C "$bf" rev-parse main)"
  check "a branch only on origin counts too" "$(cd "$bf" && base_for mip-9999/7-b)" "mip-9999/6-remote-only"

  echo
  echo "-- fork_point_for falls back to the merged PR head --"
  got="$(cd "$repo" && PATH="$tmp/bin:$PATH" merged_below mip-9999/6-board)"
  check "merged_below reads the nearest lower PR that outlived its branch" "$got" "$fork"
  got="$(cd "$repo" && PATH="$tmp/bin:$PATH" fork_point_for mip-9999/6-board "")"
  check "fork_point_for uses it when the base branch is gone" "$got" "$fork"
  got="$(cd "$repo" && PATH="$tmp/bin:$PATH" fork_point_for mip-9999/6-board mip-9999/1-solo)"
  check "and over a lower live branch's merge-base, which sits below it" "$got" "$fork"
  got="$(cd "$repo" && fork_point_for mip-9999/1-solo main)"
  check "task 1 cuts at origin/main, never at a stale local main" "$got" "$upstream"

  echo
  echo "-- restack cuts at the fork point, never at main --"
  rc=0; out="$(drive)" || rc=$?
  check "it succeeds once the merged PR names the fork point" "$rc" "0"
  check "and cuts exactly there" "$(grep -F -- '+ git rebase' <<<"$out" || true)" \
    "+ git rebase --onto origin/main $fork mip-9999/6-board"
  has "then force-pushes with a lease" "$out" "+ git push -q --force-with-lease origin mip-9999/6-board"

  { echo "#!$BASH"; echo 'exit 1'; } > "$tmp/bin/gh"   # from here on: no gh, no merged PR to find
  rc=0; out="$(drive --onto-base "$fork")" || rc=$?
  check "--onto-base overrides the lookup entirely" "$rc" "0"
  check "and is used verbatim as the cut" "$(grep -F -- '+ git rebase' <<<"$out" || true)" \
    "+ git rebase --onto origin/main $fork mip-9999/6-board"

  echo
  echo "-- restack refuses rather than replaying merged commits --"
  rc=0; out="$(drive)" || rc=$?
  check "it exits non-zero instead of rebasing blind" "$rc" "1"
  has "and names the override that unblocks it" "$out" "--onto-base"
  has "and says it could not find the fork point" "$out" "cannot tell where"
  lacks "no bare rebase onto main was planned" "$out" "git rebase origin/main"

  rc=0; out="$(drive --onto-base "$unrelated")" || rc=$?
  check "an --onto-base off another branch is refused" "$rc" "1"
  has "and is called out as not being an ancestor" "$out" "is not an ancestor of"
  lacks "with nothing rebased" "$out" "+ git rebase"

  rc=0; out="$(drive --onto-base 0000000000000000000000000000000000000000)" || rc=$?
  check "an --onto-base that is not a commit is refused" "$rc" "1"
  has "as an unfindable fork point, not a misplaced one" "$out" "cannot tell where"

  # A present base that shares no history: merge-base fails silently, which set -e made fatal before the refusal.
  rc=0; out="$(drive_as mip-9999/9-cur)" || rc=$?
  check "a base sharing no history is refused" "$rc" "1"
  has "out loud, rather than dying inside set -e" "$out" "cannot tell where"

  echo
  echo "-- new_pr_body --"
  printf '#!/bin/sh\necho "----- uprd dry run: banner"\necho "<!-- uprd: marker -->"\necho "Closes #512"\n' >"$tmp/uprd"
  chmod +x "$tmp/uprd"
  out="$(STACK_SELFTEST_UPRD="$tmp/uprd" new_pr_body main)"
  has "a new PR's body keeps uprd's Closes line after the Stacked-on prefix" "$out" "Closes #512"
  lacks "and drops only uprd's dry-run banner" "$out" "uprd dry run"

  echo
  echo "-- pr force-pushes when cost-fill rewrites an already-pushed commit (#524) --"
  # A stub stands in for cost-fill.sh (STACK_SELFTEST_COST_FILL): it deterministically amends
  # HEAD, so this proves the push decision alone, independent of cost-fill.sh's own rewrite.
  pr_origin="$tmp/pr-origin.git"; pr_work="$tmp/pr-work"
  { git init -q --bare "$pr_origin"
    git init -q -b main "$pr_work"
    git -C "$pr_work" config user.email pr@example.invalid
    git -C "$pr_work" config user.name pr-self-test
    git -C "$pr_work" remote add origin "$pr_origin"
    git -C "$pr_work" commit -q --allow-empty -m main
    git -C "$pr_work" push -q origin main
    git -C "$pr_work" checkout -q -b mip-9999/1-solo
    git -C "$pr_work" commit -q --allow-empty -m "task 1"
    git -C "$pr_work" push -q -u origin mip-9999/1-solo; } >/dev/null 2>&1
  cost_fill_stub="$tmp/cost-fill-stub"
  { echo "#!$BASH"; cat <<'SH'
set -e
echo "$*" >>"${STUB_ARGS_LOG:-/dev/null}"
[ "${1:-}" = "--dry-run" ] && { echo "cost-fill --dry-run: would rewrite these commits on stub:"; exit 0; }
git commit -q --amend --allow-empty -m "$(git log -1 --format=%B)

stub-cost-fill-rewrote-this"
SH
  } >"$cost_fill_stub"
  chmod +x "$cost_fill_stub"
  { echo "#!$BASH"; cat <<'GH'
case "$*" in
  *"pr view"*) exit 1 ;;
  *"pr create"*) echo "https://example.invalid/pull/1" ;;
  *) exit 1 ;;
esac
GH
  } >"$tmp/bin/gh"
  chmod +x "$tmp/bin/gh"
  rc=0
  out="$(cd "$pr_work" && PATH="$tmp/bin:$PATH" STACK_SELFTEST_COST_FILL="$cost_fill_stub" \
    STACK_SELFTEST_UPRD="$tmp/uprd" bash "$self" pr 2>&1)" || rc=$?
  check "the push exits 0 despite the already-pushed commit being rewritten" "$rc" "0"
  local_head="$(git -C "$pr_work" rev-parse mip-9999/1-solo)"
  remote_head="$(git --git-dir="$pr_origin" rev-parse mip-9999/1-solo)"
  check "origin ends up with the rewritten commit (force-with-lease, not a rejected push)" \
    "$remote_head" "$local_head"

  echo
  echo "-- pr on a repo's first row of a MIP bases on main, not on nothing (#13) --"
  { git -C "$pr_work" checkout -q -b mip-9998/3-first main
    git -C "$pr_work" commit -q --allow-empty -m "task 3"; } >/dev/null 2>&1
  rc=0
  out="$(cd "$pr_work" && PATH="$tmp/bin:$PATH" STACK_SELFTEST_BRANCH=mip-9998/3-first \
    STACK_SELFTEST_COST_FILL="$cost_fill_stub" STACK_SELFTEST_UPRD="$tmp/uprd" bash "$self" --dry-run pr 2>&1)" || rc=$?
  check "it exits 0" "$rc" "0"
  has "and opens the PR against main" "$out" "+ gh pr create --base main --head mip-9998/3-first"
  git -C "$pr_work" checkout -q mip-9999/1-solo >/dev/null 2>&1

  echo
  echo "-- pr still sets upstream on a brand-new branch's first push (#524) --"
  # The common case: a task branch's very first scripts/stack.sh pr, never pushed before, where
  # cost-fill almost always rewrites something (a bare Cost:/Tested: placeholder, at least). The
  # force-with-lease branch must keep -u too, or the branch ends up with no upstream configured.
  fp_origin="$tmp/fp-origin.git"; fp_work="$tmp/fp-work"
  { git init -q --bare "$fp_origin"
    git init -q -b main "$fp_work"
    git -C "$fp_work" config user.email fp@example.invalid
    git -C "$fp_work" config user.name fp-self-test
    git -C "$fp_work" remote add origin "$fp_origin"
    git -C "$fp_work" commit -q --allow-empty -m main
    git -C "$fp_work" push -q origin main
    git -C "$fp_work" checkout -q -b mip-9999/1-fresh
    git -C "$fp_work" commit -q --allow-empty -m "task 1"; } >/dev/null 2>&1
  rc=0
  out="$(cd "$fp_work" && PATH="$tmp/bin:$PATH" STACK_SELFTEST_COST_FILL="$cost_fill_stub" \
    STACK_SELFTEST_UPRD="$tmp/uprd" bash "$self" pr 2>&1)" || rc=$?
  check "the first-ever push still exits 0" "$rc" "0"
  rc=0
  upstream_ref="$(git -C "$fp_work" rev-parse --abbrev-ref mip-9999/1-fresh@\{upstream\} 2>&1)" || rc=$?
  check "and @{upstream} ends up set despite the force-with-lease branch" "$rc" "0"
  check "to origin's copy of the branch" "$upstream_ref" "origin/mip-9999/1-fresh"

  echo
  echo "-- pr hands cost-fill the task's own base, not origin/main (#524) --"
  { git -C "$pr_work" checkout -q -b mip-9999/2-next mip-9999/1-solo
    git -C "$pr_work" commit -q --allow-empty -m "task 2"
    git -C "$pr_work" fetch -q origin; } >/dev/null 2>&1
  out="$(cd "$pr_work" && PATH="$tmp/bin:$PATH" STACK_SELFTEST_COST_FILL="$cost_fill_stub" \
    STUB_ARGS_LOG="$tmp/stub-args" STACK_SELFTEST_UPRD="$tmp/uprd" bash "$self" --dry-run pr 2>&1)" || true
  check "task 2's fork point is task 1's pushed tip" "$(cat "$tmp/stub-args" 2>/dev/null)" \
    "--dry-run --base $(git -C "$pr_work" rev-parse origin/mip-9999/1-solo)"

  echo
  # Task 1 squash-merged and task 2 rebased onto main, so the parent-derived fork point is stale:
  # "stale" leaves the deleted branch's tracking ref behind (fetch doesn't prune), "pruned" drops it
  # so fork_point_for asks gh, which answers with task 1's pre-squash head.
  for variant in stale pruned; do
  echo "-- pr on a restacked child rewrites only its own commits, $variant parent ref (#524) --"
  rs_origin="$tmp/rs-$variant-origin.git"; rs_work="$tmp/rs-$variant-work"
  { git init -q --bare "$rs_origin"
    git init -q -b main "$rs_work"
    git -C "$rs_work" config user.email rs@example.invalid
    git -C "$rs_work" config user.name rs-self-test
    git -C "$rs_work" remote add origin "$rs_origin"
    git -C "$rs_work" commit -q --allow-empty -m $'main\n\nTested: x\nCost: $0\nCo-Authored-By: C <c@x.invalid>'
    git -C "$rs_work" push -q origin main
    git -C "$rs_work" checkout -q -b mip-9999/1-a
    echo a >"$rs_work/a.txt"; git -C "$rs_work" add a.txt
    git -C "$rs_work" commit -q -m $'task 1\n\nTested: x\nCost: $0\nCo-Authored-By: C <c@x.invalid>'
    git -C "$rs_work" push -q -u origin mip-9999/1-a
    git -C "$rs_work" checkout -q -b mip-9999/2-b
    echo b >"$rs_work/b.txt"; git -C "$rs_work" add b.txt
    git -C "$rs_work" commit -q -m $'task 2\n\nTested: x\nCost: $0\nCo-Authored-By: C <c@x.invalid>'
    git -C "$rs_work" checkout -q main
    git -C "$rs_work" merge -q --squash mip-9999/1-a
    GIT_COMMITTER_NAME=GitHub git -C "$rs_work" commit -q -m $'task 1 (#1)\n\nTested: x\nCost: $0\nCo-Authored-By: C <c@x.invalid>'
    git -C "$rs_work" push -q origin main
    git --git-dir="$rs_origin" update-ref -d refs/heads/mip-9999/1-a
    t1_head="$(git -C "$rs_work" rev-parse mip-9999/1-a)"
    if [ "$variant" = pruned ]; then
      git -C "$rs_work" update-ref -d refs/remotes/origin/mip-9999/1-a
      git -C "$rs_work" branch -q -D mip-9999/1-a
    fi
    git -C "$rs_work" rebase -q --onto origin/main "$t1_head" mip-9999/2-b
    echo c >"$rs_work/c.txt"; git -C "$rs_work" add c.txt
    git -C "$rs_work" commit -q -m $'review fix\n\nTested: x\nCost: est. pending\nCo-Authored-By: C <c@x.invalid>'
  } >/dev/null 2>&1
  mkdir -p "$tmp/rs-bin"
  printf '#!%s\ncase "$*" in *"pr list"*) echo %s ;; *"pr create"*) echo https://example.invalid/pull/1 ;; *) exit 1 ;; esac\n' \
    "$BASH" "$t1_head" >"$tmp/rs-bin/gh"; chmod +x "$tmp/rs-bin/gh"
  main_tip="$(git -C "$rs_work" rev-parse origin/main)"
  out="$(cd "$rs_work" && PATH="$tmp/rs-bin:$PATH" STACK_SELFTEST_COST_FILL="$(dirname "$self")/cost-fill.sh" \
    STACK_SELFTEST_UPRD="$tmp/uprd" bash "$self" --dry-run pr 2>&1)" || true
  has "--dry-run replays only the child's two commits" "$out" "replaying 2 commit(s)"
  out="$(cd "$rs_work" && PATH="$tmp/rs-bin:$PATH" STACK_SELFTEST_COST_FILL="$(dirname "$self")/cost-fill.sh" \
    STACK_SELFTEST_UPRD="$tmp/uprd" bash "$self" pr 2>&1)" || true
  has "and so does the real run" "$out" "across 2 commit(s)"
  check "origin/main stays an ancestor" \
    "$(git -C "$rs_work" merge-base --is-ancestor "$main_tip" mip-9999/2-b && echo yes || echo no)" "yes"
  check "with exactly the child's commits on top" "$(git -C "$rs_work" rev-list --count "$main_tip..mip-9999/2-b")" "2"
  done

  echo
  if [ "$failed" -eq 1 ]; then echo "stack self-test: FAILED" >&2; return 1; fi
  echo "stack self-test: ok"
}

[ "$selftest" -eq 1 ] && { self_test; exit 0; }

case "${1:-}" in
  start)
    mip="$(tr 'A-Z' 'a-z' <<<"${2:?MIP-NNNN}")"; k="${3:?task number}"; slug="${4:?slug}"
    new="$mip/$k-$slug"
    base="$(base_for "$new")"
    run git fetch -q origin
    # base_for also finds a branch that exists only on origin.
    if [ "$base" = main ]; then from=origin/main
    elif git rev-parse --verify -q "refs/heads/$base" >/dev/null; then from="$base"
    else from="origin/$base"; fi
    run git checkout -q -b "$new" "$from"
    echo "started $new (base: $base)"
    ;;
  pr)
    base="$(base_for "$cur")"
    [ -n "$base" ] || { echo "stack pr: found no base for $cur; nothing pushed" >&2; exit 1; }
    # cost-fill.sh runs here, not in scripts/pr.sh (#524): this is the documented primary path
    # for a mip task branch (.claude/skills/mip-tasks/SKILL.md), and it decides on its own
    # whether anything needs a trailer upgrade or a Closes #N line. A rewritten HEAD needs
    # --force-with-lease, same as restack below, since the branch may already be pushed.
    cost_fill="${STACK_SELFTEST_COST_FILL:-$script_dir/cost-fill.sh}"
    # The parent's pushed tip, since that is what the PR diffs against; without it cost-fill's
    # origin/main range would re-amend the parent's commits and see the parent's Closes line.
    cf_ref="$base"; [ "$base" = main ] || ! git rev-parse --verify -q "origin/$base^{commit}" >/dev/null || cf_ref="origin/$base"
    cf_base="$(fork_point_for "$cur" "$cf_ref")" || cf_base=""
    # fork_point_for may answer with gh's merged head sha, which a squash-merge leaves off HEAD's history.
    [ -z "$cf_base" ] || cf_base="$(git merge-base HEAD "$cf_base" 2>/dev/null)" || cf_base=""
    # After a squash-merge + restack the parent's ref is stale and points below main; take
    # whichever fork point is nearer HEAD, or cost-fill would replay main's own commits.
    mb="$(git merge-base HEAD origin/main)" || mb=""
    if [ -z "$cf_base" ] || { [ -n "$mb" ] && git merge-base --is-ancestor "$cf_base" "$mb"; }; then cf_base="$mb"; fi
    cf_args=(); [ -z "$cf_base" ] || cf_args=(--base "$cf_base")
    if [ "$dry" -eq 1 ]; then
      echo "+ $cost_fill ${cf_args[*]}"
      cf_out="$("$cost_fill" --dry-run "${cf_args[@]}")"
      echo "$cf_out"
      if grep -q '^cost-fill --dry-run: would rewrite' <<<"$cf_out"; then
        push=(git push -q -u --force-with-lease origin "$cur")
      else
        push=(git push -q -u origin "$cur")
      fi
    else
      before_head="$(git rev-parse HEAD)"
      "$cost_fill" "${cf_args[@]}"
      if [ "$before_head" != "$(git rev-parse HEAD)" ]; then
        push=(git push -q -u --force-with-lease origin "$cur")
      else
        push=(git push -q -u origin "$cur")
      fi
    fi
    run "${push[@]}"
    if gh pr view "$cur" --json number -q .number >/dev/null 2>&1; then
      run gh pr edit "$cur" --base "$base"
      run "$script_dir/uprd.sh"
    else
      title="$(git log --reverse --format=%s "origin/$base..HEAD" 2>/dev/null | head -1)" || title=""
      [ -n "$title" ] || title="$(git log -1 --format=%s)"
      body="$(mktemp)"; new_pr_body "$base" > "$body"
      run gh pr create --base "$base" --head "$cur" --title "$title" --body-file "$body"
    fi
    ;;
  restack)
    old_base="$(base_for "$cur")"
    run git fetch -q origin
    fork="${onto_base:-$(fork_point_for "$cur" "$old_base")}" || true   # a failed lookup must reach the guard, not set -e
    if [ -z "$fork" ] || ! git rev-parse --verify -q "$fork^{commit}" >/dev/null; then
      { echo "restack: cannot tell where $cur's own commits begin."
        echo "  Its base (${old_base:-the previous task branch}) yields no merge-base — GitHub deletes a branch when its PR merges —"
        echo "  and gh named no merged PR for a lower $(mip_of "$cur") task within the last $pr_window merged ones"
        echo "  (it may equally be unavailable or logged out here — a jail has no gh login)."
        echo "  Rebasing onto main without that point would replay the already-merged commits and conflict on each."
        echo "  Pass the previous task branch's last commit yourself:"
        echo "    stack restack --onto-base <sha>"; } >&2
      exit 1
    fi
    if ! git merge-base --is-ancestor "$fork" "$cur" 2>/dev/null; then
      { echo "restack: ${fork:0:7} is not an ancestor of $cur, so it cannot be where its commits begin."
        echo "  Cutting there would replay unrelated history onto main and force-push the result."
        echo "  Pass the previous task branch's last commit instead:"
        echo "    stack restack --onto-base <sha>"; } >&2
      exit 1
    fi
    run git rebase --onto origin/main "$fork" "$cur"
    run git push -q --force-with-lease origin "$cur"
    echo "restacked $cur onto main (cut at ${fork:0:7}${old_base:+, old base $old_base})"
    ;;
  status)
    mip="${2:-$(mip_of "$cur")}"; mip="$(tr 'A-Z' 'a-z' <<<"$mip")"
    for b in $(git branch --list "$mip/*" --format='%(refname:short)' | sort -t/ -k2 -n); do
      pr="$(gh pr view "$b" --json number,state -q '"#\(.number) \(.state)"' 2>/dev/null || echo "no PR")"
      printf '%-40s base=%-28s %s  (%s ahead of main)\n' "$b" "$(base_for "$b")" "$pr" "$(git rev-list --count origin/main.."$b")"
    done
    ;;
  branches)
    branches_of "$(mip_arg "${2:-}")"
    ;;
  link)
    # GitHub's native Stack (the "Preview stack" box on a PR) through the official extension.
    # stack_link (scripts/lib/stack_link.sh, shared with scripts/deps-stack.sh) does the open-PR
    # filtering and the actual `gh stack link` call.
    mip="$(mip_arg "${2:-}")"
    # shellcheck source=scripts/lib/stack_link.sh.
    source "$(dirname "${BASH_SOURCE[0]}")/lib/stack_link.sh"
    mapfile -t brs < <(branches_of "$mip")
    stack_link "$dry" "${brs[@]}"
    ;;
  *) awk 'NR > 1 { if (!/^#/) exit; print }' "$0"; exit 1 ;;
esac
