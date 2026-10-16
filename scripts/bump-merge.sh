#!/usr/bin/env bash
# bump-merge — squash-merge the devkit's pin-bump PRs in every consumer, with no person, under rules (#77).
#
#   scripts/bump-merge.sh [--dry-run] [--now EPOCH]   # bump-merge.yml, hourly, as marola-merge-bot
#   scripts/bump-merge.sh --self-test                 # a fake gh on PATH and git fixtures, no network
#
# Only the newest devkit release (releases/latest) is eligible; a bump PR for another tag is refused as
# not the latest release, and if that release is not the highest v* tag (a backport) nothing is judged.
# A PR waits quietly for BUMP_SOAK_HOURS (24) after the release, and every consumer but
# marola-dev/marola waits until the umbrella's main pins the tag and is green. It is refused, with one
# comment per identical body, for a major (the tag's first number above every earlier v* tag's), a head
# branch other than chore/devkit-vX.Y.Z in the repo itself, an author other than marola-merge-bot[bot],
# a PR that is not one commit on the default branch, a change bump_check.py refuses, a moved head or a
# red check. A `hold` label skips a PR; pending checks wait. AUTO_MERGE=false merges nothing. The merge
# is pinned to the head the checks and the content check read (--match-head-commit). Each consumer is
# judged in its own process: one failing ends the run red, never merges that repo, and spares the rest.
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "$SELF")/.." && pwd)"
CANARY=marola-dev/marola
DEVKIT=marola-dev/marola-devkit
BOT='marola-merge-bot[bot]'

# The App token is installation-scoped to the consumers, so the devkit's releases are read with
# GH_READ_TOKEN (github.token in CI); unset, gh falls back to its own login.
read_gh() { GH_TOKEN="${GH_READ_TOKEN:-}" gh "$@" </dev/null; }
api() { gh api "$@" </dev/null; }

# checks_state <repo> <ref>: CS is "ok", "wait <names>" or "red <names>". Check runs are
# filter=latest by default, so a re-run replaces its failed attempt; only .statuses counts for the
# combined status, whose own .state reads "pending" with none. ci-ok must come from Actions (app 15368).
checks_state() {
  local runs from_runs from_statuses checks failed pending ci_ok
  runs="$(api --paginate "repos/$1/commits/$2/check-runs?per_page=100" | jq -sc '{check_runs: [.[].check_runs[]]}')"
  # Each part in its own assignment: errexit does not reach the first command of a compound `$(...)`.
  from_runs="$(jq -r '.check_runs[] | [.name, .status, (.conclusion // "")] | @tsv' <<<"$runs")"
  from_statuses="$(api --paginate "repos/$1/commits/$2/status?per_page=100" |
    jq -rs '.[].statuses[] | [.context, (if .state == "pending" then "pending" else "completed" end), .state] | @tsv')"
  checks="$(printf '%s\n%s\n' "$from_runs" "$from_statuses" | sed '/^$/d')"
  failed="$(awk -F'\t' '$2 == "completed" && $3 !~ /^(success|skipped|neutral)$/ { printf "%s`%s` (%s)", sep, $1, $3; sep = ", " }' <<<"$checks")"
  pending="$(awk -F'\t' '$2 != "completed" { printf "%s%s", sep, $1; sep = ", " }' <<<"$checks")"
  ci_ok="$(jq -r '[.check_runs[] | select(.name == "ci-ok" and .app.id == 15368 and .status == "completed")][0].conclusion // empty' <<<"$runs")"
  if [ -n "$failed" ]; then
    CS="red $failed"
  elif [ -z "$checks" ] || [ -n "$pending" ] || [ "$ci_ok" != success ]; then
    CS="wait ${pending:-ci-ok}"
  else
    CS=ok
  fi
}

# canary_why: empty once the umbrella is green on $tag, else why not. Judged once per consumer process.
canary_why="" canary_done=0
canary_check() {
  [ "$canary_done" -eq 0 ] || return 0
  canary_done=1
  local def sha merged flake runs bad
  def="$(api "repos/$CANARY" | jq -er .default_branch)"
  # One commit for the pin and the checks: main moves between three reads of a branch name.
  sha="$(api "repos/$CANARY/commits/$def" | jq -er .sha)"
  flake="$(api -H 'Accept: application/vnd.github.raw+json' "repos/$CANARY/contents/flake.nix?ref=$sha")"
  if ! grep -qF "marola-devkit/$tag\"" <<<"$flake"; then
    canary_why="the umbrella's $def does not pin $tag yet"
    return 0
  fi
  checks_state "$CANARY" "$sha"
  if [ "$CS" != ok ]; then
    canary_why="the umbrella's $def is not green yet (${CS%% *})"
    return 0
  fi
  merged="$(api --paginate "repos/$CANARY/pulls?state=closed&head=${CANARY%%/*}:chore/devkit-$tag&per_page=100" |
    jq -rs '[.[][] | select(.merged_at != null)][0].merge_commit_sha // empty')"
  [ -n "$merged" ] || return 0
  # Read first: in an `if` condition errexit is off, and a failed read would pass as "no failure".
  runs="$(api --paginate "repos/$CANARY/commits/$merged/check-runs?per_page=100" | jq -sc '[.[].check_runs[]]')"
  # Cancelled is fine: main's CI cancels the runs of the commit before it. Any other finished
  # conclusion (failure, timed_out, action_required, stale, ...) is a failure.
  bad="$(jq -r 'any(.[]; .status == "completed" and (.conclusion | IN("success", "skipped", "neutral", "cancelled") | not))' <<<"$runs")"
  if [ "$bad" != false ]; then
    canary_why="a check run on the umbrella's merge commit of $tag failed"
  fi
}

# refuse <why> [<what next>]: one comment per identical body, then the PR is left alone.
refuse() {
  local body="Not auto-merged at \`${head:0:7}\`: $1. ${2:-Left for a person} (#77)."
  echo "::warning::$repo #$n $body"
  echo "bump-merge: $repo #$n: refused: $1"
  local seen rc=0
  if ! seen="$(gh pr view "$n" -R "$repo" --json comments </dev/null)"; then
    rc=2
  else
    jq -e --arg b "$body" 'any(.comments[]; .body == $b)' <<<"$seen" >/dev/null || rc=$?
  fi
  # jq -e: 0 is "already said", 1 is "not said"; anything else is a read that cannot be trusted.
  case "$rc" in
    0) echo "bump-merge: $repo #$n: already says so" ;;
    1)
      if [ "$dry" -eq 1 ]; then
        echo "+ gh pr comment $n -R $repo --body '$body'"
      else
        gh pr comment "$n" -R "$repo" --body "$body" </dev/null
      fi
      ;;
    *) echo "bump-merge: $repo #$n: could not read its comments; not commenting" ;;
  esac
}
wait_for() { echo "bump-merge: $repo #$n: waits: $1"; }

# judge: one bump PR of $repo; $n, $head, $ref, $author, $fork, $base, $held and $def are its facts.
judge() {
  local ptag why="" d msg rc parent extra line pulled mb cur
  if [ "$held" = true ]; then
    echo "bump-merge: $repo #$n: held; left for a person"
    return 0
  fi
  if [[ "$ref" =~ ^chore/devkit-(v[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then ptag="${BASH_REMATCH[1]}"; else ptag=""; fi
  if [ -z "$ptag" ]; then
    why="the head branch \`$ref\` is not \`chore/devkit-vX.Y.Z\`"
  elif [ "$fork" = true ]; then
    why="the head is in a fork"
  elif [ "$base" != "$def" ]; then
    why="the base is \`$base\`, not the default branch \`$def\`"
  elif [ "$author" != "$BOT" ]; then
    why="the author is \`$author\`, not \`$BOT\`"
  fi
  if [ -n "$why" ]; then refuse "$why"; return 0; fi
  if [ "$ptag" != "$tag" ]; then
    refuse "$ptag is not the latest release $tag" "Left for a person to close"
    return 0
  fi
  if [ "$major" -eq 1 ]; then
    refuse "$tag is a major release" "A major waits for a person"
    return 0
  fi
  # Arithmetic, not `[ -lt ]`: an error there reads as false, and false must wait.
  if ! ((now >= soak_end)); then
    wait_for "soak until $(date -u -d "@$soak_end" +%FT%TZ)"
    return 0
  fi
  if [ "$repo" != "$CANARY" ]; then
    canary_check
    if [ -n "$canary_why" ]; then wait_for "$canary_why"; return 0; fi
  fi

  checks_state "$repo" "$head"
  case "$CS" in
    red*) refuse "not every check passed: ${CS#red }"; return 0 ;;
    wait*) wait_for "${CS#wait }"; return 0 ;;
  esac

  d="$work/${repo//\//_}"
  if [ ! -d "$d" ]; then git clone -q --no-checkout --no-tags "${BUMP_CONSUMERS_URL:-https://github.com}/$repo" "$d" </dev/null; fi
  git -C "$d" fetch -q --no-tags origin "+refs/pull/$n/head:refs/remotes/origin/pull/$n" "+refs/heads/$def:refs/remotes/origin/$def" </dev/null
  pulled="$(git -C "$d" rev-parse "refs/remotes/origin/pull/$n")"
  if [ "$pulled" != "$head" ]; then
    refuse "the head moved while this run read it" "The new head's checks retry it"
    return 0
  fi
  # The squash diff is the head commit's own only when its parent is where the PR left the default branch.
  # These reads are assigned first, so a git failure ends the consumer red instead of reading as a refusal.
  line="$(git -C "$d" rev-list --parents -n 1 "$head")"
  read -r _ parent extra <<<"$line"
  mb=""
  if [ -n "$parent" ] && [ -z "$extra" ]; then mb="$(git -C "$d" merge-base "refs/remotes/origin/$def" "$head")"; fi
  if [ -z "$mb" ] || [ "$mb" != "$parent" ]; then
    refuse "commits: the PR is not one commit on top of \`$def\`"
    return 0
  fi
  rc=0
  msg="$(python3 "$root/scripts/bump_check.py" "$d" "$parent" "$head" "${tag#v}" "$rev" 2>&1 >/dev/null)" || rc=$?
  # 1 is a rule refusing the patch; anything else (bad input, bump_check.py exiting 2 on a crash, no
  # python) is an error, not a verdict on the PR.
  case "$rc" in
    0) ;;
    1)
      refuse "the content check refused it: $msg"
      return 0
      ;;
    *)
      echo "::error::bump-merge: $repo #$n: bump_check.py exited $rc: $msg" >&2
      return 1
      ;;
  esac

  # A hold or a push since the listing: wait for the next run, which reads it all again.
  cur="$(api "repos/$repo/pulls/$n")"
  if ! jq -e --arg h "$head" '.state == "open" and .head.sha == $h and (any(.labels[]?; .name == "hold") | not)' <<<"$cur" >/dev/null; then
    wait_for "the PR was closed, held or pushed to since it was listed"
    return 0
  fi
  # --admin only skips gh's client-side refusal of a BLOCKED PR; GitHub still decides, and lets this
  # App through as main-rule's pull-request-only bypass actor. --match-head-commit aborts the merge
  # if anything was pushed since the checks ran.
  local merge=(gh pr merge "$n" --squash --admin --match-head-commit "$head" -R "$repo")
  if [ "$dry" -eq 1 ]; then
    echo "+ ${merge[*]}"
    echo "bump-merge: $repo #$n: would merge at ${head:0:7}"
  else
    "${merge[@]}" </dev/null
    echo "bump-merge: $repo #$n: merged at ${head:0:7}"
  fi
}

# consumer <repo>: judge every open bump PR of one repo.
consumer() {
  local repo="$1" def prs count i pr n head ref author fork base held
  def="$(api "repos/$repo" | jq -er .default_branch)"
  prs="$(api --paginate "repos/$repo/pulls?state=open&per_page=100" | jq -sc --arg repo "$repo" '
    [.[][] | select(.head.ref | startswith("chore/devkit-v"))
      | {n: .number, ref: .head.ref, head: .head.sha, fork: ((.head.repo.full_name // "") != $repo),
         base: .base.ref, author: (.user.login // ""), held: any(.labels[]?; .name == "hold")}]
    | sort_by(.n)')"
  count="$(jq length <<<"$prs")"
  if [ "$count" -eq 0 ]; then
    echo "bump-merge: $repo: nothing open"
    return 0
  fi
  for ((i = 0; i < count; i++)); do
    pr="$(jq -c ".[$i]" <<<"$prs")"
    n="$(jq -r .n <<<"$pr")" head="$(jq -r .head <<<"$pr")" ref="$(jq -r .ref <<<"$pr")"
    author="$(jq -r .author <<<"$pr")" fork="$(jq -r .fork <<<"$pr")" base="$(jq -r .base <<<"$pr")"
    held="$(jq -r .held <<<"$pr")"
    judge
  done
}

run() {
  dry="$1" now="$2"
  if [ "${AUTO_MERGE:-}" = false ]; then
    echo "bump-merge: AUTO_MERGE is false; nothing merged"
    return 0
  fi
  local list_text rel published published_at soak_hours earlier num tags vers repo r list=() ordered=() failed=0
  rel="$(read_gh api "repos/$DEVKIT/releases/latest")"
  tag="$(jq -er .tag_name <<<"$rel")"
  published="$(jq -er .published_at <<<"$rel")"
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::the latest devkit release, $tag, is not vX.Y.Z" >&2; return 1; }
  published_at="$(date -d "$published" +%s)"
  soak_hours="${BUMP_SOAK_HOURS:-24}"
  [[ "$published_at" =~ ^[0-9]+$ && "$soak_hours" =~ ^[0-9]+$ ]] ||
    { echo "::error::bad release time '$published' or BUMP_SOAK_HOURS '$soak_hours'" >&2; return 1; }
  soak_end=$((published_at + soak_hours * 3600))
  rev="$("$root/scripts/bump-consumers.sh" tag-rev "$tag")" || { echo "::error::no tag $tag in the devkit" >&2; return 1; }
  num="${tag#v}"
  # A failed listing must not read as "no earlier tag": that would call a major a patch.
  tags="$(git ls-remote --tags --refs "${BUMP_DEVKIT_URL:-https://github.com/$DEVKIT}" 'refs/tags/v*')" ||
    { echo "::error::could not list the devkit's tags" >&2; return 1; }
  vers="$(sed -nE 's#.*refs/tags/v([0-9]+\.[0-9]+\.[0-9]+)$#\1#p' <<<"$tags" | sort -uV)"
  grep -qxF "$num" <<<"$vers" || { echo "::error::no tag $tag in the devkit's tag list" >&2; return 1; }
  # Major: the tag's first number is above the highest earlier v* tag's.
  earlier="$(sed "/^${num//./\\.}\$/,\$d" <<<"$vers" | tail -1)"
  major=0
  if [ -n "$earlier" ] && [ "${num%%.*}" -gt "${earlier%%.*}" ]; then major=1; fi
  # releases/latest is the newest published, which can be a backport; its PRs would downgrade a repo.
  if [ "$(tail -1 <<<"$vers")" != "$num" ]; then
    echo "bump-merge: waits: $tag is the latest release but not the highest v* tag, v$(tail -1 <<<"$vers"); nothing is judged"
    return 0
  fi

  # The canary first: whatever it merges this run is seen by the others on the next one.
  list_text="$("$root/scripts/bump-consumers.sh" repos)" || { echo "::error::could not read the consumer list" >&2; return 1; }
  [ -n "$list_text" ] || { echo "::error::the consumer list is empty" >&2; return 1; }
  mapfile -t list <<<"$list_text"
  for r in "${list[@]}"; do [ "$r" != "$CANARY" ] || ordered+=("$r"); done
  for r in "${list[@]}"; do [ "$r" = "$CANARY" ] || ordered+=("$r"); done

  work="$(mktemp -d)"
  trap 'rm -rf "$work"' RETURN
  # Each consumer in its own process, as bump-consumers does: errexit is off inside `||`, so a failed
  # read there would go on to merge. A failure ends only that repo, and the run ends red.
  for repo in "${ordered[@]}"; do
    consumer "$repo" &
    wait $! || { echo "::error::bump-merge: $repo failed; nothing more was merged there" >&2; failed=1; }
  done
  return "$failed"
}

self_test() {
  # A pre-push hook exports GIT_DIR (and a worktree, GIT_COMMON_DIR); the fixtures below would
  # otherwise commit into the caller's own repo.
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
  local t fails=0
  t="$(mktemp -d)"
  trap 'rm -rf "$t"' RETURN
  ok() { if [ "$1" = "$2" ]; then echo "  ok   $3"; else echo "  FAIL $3 — got '$1' want '$2'"; fails=$((fails + 1)); fi; }
  printf '[user]\n\tname = t\n\temail = t@t\n[protocol "file"]\n\tallow = always\n' >"$t/gitconfig"
  export GIT_CONFIG_GLOBAL="$t/gitconfig" GIT_CONFIG_NOSYSTEM=1

  # The fake gh serves $FAKE_GH/<repo name>/{pulls,closed,comments}.json, cr-<ref>.json, st-<ref>.json and
  # flake.nix, plus latest.json for the devkit's release, and logs what it was asked to change.
  mkdir -p "$t/bin" "$t/gh"
  cat >"$t/bin/gh" <<EOF
#!$BASH
set -euo pipefail
d="\$FAKE_GH"
echo "\$*" >>"\$d/all"
slug_of() { local s="\${1#repos/marola-dev/}"; echo "\${s%%/*}"; }
case "\$1" in
  api)
    ep="\${*: -1}"
    case "\$ep" in
      repos/$DEVKIT/releases/latest) [ "\$GH_TOKEN" = read ] && cat "\$d/latest.json" ;;
      repos/*/pulls?state=closed*) cat "\$d/\$(slug_of "\$ep")/closed.json" ;;
      repos/*/pulls/[0-9]*)
        jq -c --argjson n "\${ep##*/}" --argjson h "\$([ -f "\$d/late-hold" ] && echo true || echo false)" \\
          '.[] | select(.number == \$n) | . + {state: "open"} | if \$h then .labels += [{name: "hold"}] else . end' \\
          "\$d/\$(slug_of "\$ep")/pulls.json" ;;
      repos/*/pulls?*)
        [ ! -f "\$d/\$(slug_of "\$ep")/fail-pulls" ] || exit 1
        cat "\$d/\$(slug_of "\$ep")/pulls.json" ;;
      repos/*/contents/flake.nix*) cat "\$d/\$(slug_of "\$ep")/flake.nix" ;;
      repos/*/commits/main) echo '{"sha":"mainsha"}' ;;
      repos/*/commits/*/check-runs*)
        [ ! -f "\$d/\$(slug_of "\$ep")/fail-cr" ] || exit 1
        x="\${ep#*/commits/}"; f="\$d/\$(slug_of "\$ep")/cr-\${x%%/*}.json"
        [ ! -f "\$d/\$(slug_of "\$ep")/fail-cr-\${x%%/*}" ] || exit 1
        if [ -f "\$f" ]; then cat "\$f"; else echo '{"check_runs":[]}'; fi ;;
      repos/*/commits/*/status*)
        x="\${ep#*/commits/}"; f="\$d/\$(slug_of "\$ep")/st-\${x%%/*}.json"
        if [ -f "\$f" ]; then cat "\$f"; else echo '{"statuses":[]}'; fi ;;
      repos/marola-dev/*) echo '{"default_branch":"main"}' ;;
      *) echo "fake gh: unexpected: \$*" >&2; exit 1 ;;
    esac ;;
  pr)
    for ((i = 1; i < \$#; i++)); do [ "\${!i}" = -R ] && j=\$((i + 1)) && s="\$(slug_of "repos/\${!j}")"; done
    case "\$2" in
      view) [ ! -f "\$d/\$s/fail-view" ] || exit 1
        if [ -f "\$d/\$s/bad-view" ]; then echo 'not json'; exit 0; fi
        cat "\$d/\$s/comments.json" ;;
      comment)
        printf '%s' "\${*: -1}" >"\$d/last-comment"
        jq --arg b "\${*: -1}" '.comments += [{body: \$b}]' "\$d/\$s/comments.json" >"\$d/c.tmp" && mv "\$d/c.tmp" "\$d/\$s/comments.json"
        echo "comment \$s \$3" >>"\$d/calls" ;;
      merge) echo "merge \$s \$3 \${*:4}" >>"\$d/calls" ;;
      *) echo "fake gh: unexpected: \$*" >&2; exit 1 ;;
    esac ;;
  *) echo "fake gh: unexpected: \$*" >&2; exit 1 ;;
esac
EOF
  # A tag listing that fails, on demand.
  # shellcheck disable=SC2016
  printf '#!%s\nif [ -n "${FAKE_LS_FAIL:-}" ] && [ "$1" = ls-remote ] && [[ " $* " == *" --refs "* ]]; then exit 128; fi\nexec %s "$@"\n' \
    "$BASH" "$(command -v git)" >"$t/bin/git"
  # A bump_check.py that crashes (exit 2, as the real one does, not its refusal 1), on demand.
  # shellcheck disable=SC2016
  printf '#!%s\nif [ -n "${FAKE_PY_FAIL:-}" ] && [[ "$1" == *bump_check.py ]]; then echo boom >&2; exit 2; fi\nexec %s "$@"\n' \
    "$BASH" "$(command -v python3)" >"$t/bin/python3"
  chmod +x "$t/bin/gh" "$t/bin/git" "$t/bin/python3"
  export PATH="$t/bin:$PATH" FAKE_GH="$t/gh" GH_TOKEN=app GH_READ_TOKEN=read
  unset AUTO_MERGE BUMP_SOAK_HOURS

  # The devkit: v0.10.0 then patches, and a v1.0.0.
  export BUMP_DEVKIT_URL="file://$t/devkit" BUMP_CONSUMERS_URL="file://$t/remote" BUMP_CONSUMERS_FILE="$t/consumers.txt"
  git init -q -b main "$t/devkit"
  local v
  declare -A dc=()
  for v in 0.9.0 0.10.0 0.10.1 0.10.2 1.0.0; do
    git -C "$t/devkit" commit -q --allow-empty -m "devkit $v"
    dc[$v]="$(git -C "$t/devkit" rev-parse HEAD)"
  done
  # retag <versions>: the devkit has exactly these tags (the highest is what releases/latest may name).
  retag() {
    local x
    for x in $(git -C "$t/devkit" tag); do git -C "$t/devkit" tag -d "$x" >/dev/null; done
    for x in "$@"; do git -C "$t/devkit" tag -a -m "v$x" "v$x" "${dc[$x]}"; done
  }
  printf 'marola-dev/marola\nmarola-dev/marola-app\n' >"$t/consumers.txt"

  # Two consumers, each a bare remote with a base pinning v0.10.0 and one bump branch per variant.
  local r w sha
  declare -A head=()
  for r in marola marola-app; do
    mkdir -p "$t/remote/marola-dev" "$t/gh/$r"
    git init -q -b main "$t/seed-$r"
    echo 'inputs.marola-devkit.url = "github:marola-dev/marola-devkit/v0.10.0";' >"$t/seed-$r/flake.nix"
    echo readme >"$t/seed-$r/README.md"
    git -C "$t/seed-$r" add -A && git -C "$t/seed-$r" commit -q -m base
    git clone -q --bare "$t/seed-$r" "$t/remote/marola-dev/$r"
    w="$t/seed-$r"
    for v in 0.10.1 0.10.2 1.0.0; do
      git -C "$w" switch -q -C "bump-$v" main
      python3 "$root/scripts/release.py" --consumer "$w" "$v" >/dev/null
      git -C "$w" commit -qam "chore: marola-devkit v$v"
      head[$r-$v]="$(git -C "$w" rev-parse HEAD)"
    done
    git -C "$w" switch -q -C bump-outside bump-0.10.1
    echo more >>"$w/README.md"
    git -C "$w" commit -qa --amend --no-edit
    head[$r-outside]="$(git -C "$w" rev-parse HEAD)"
    git -C "$w" switch -q -C bump-two main
    git -C "$w" commit -q --allow-empty -m first
    python3 "$root/scripts/release.py" --consumer "$w" 0.10.1 >/dev/null
    git -C "$w" commit -qam "chore: marola-devkit v0.10.1"
    head[$r-two]="$(git -C "$w" rev-parse HEAD)"
    git -C "$w" push -q "$t/remote/marola-dev/$r" --all
  done

  local published=2026-10-15T00:00:00Z epoch after before
  epoch="$(date -d "$published" +%s)"
  after=$((epoch + 25 * 3600))
  before=$((epoch + 3600))
  local green red_ci pending_ci
  green='{"check_runs":[{"name":"changes","status":"completed","conclusion":"success"},{"name":"python-ci","status":"completed","conclusion":"skipped"},{"name":"ci-ok","status":"completed","conclusion":"success","app":{"id":15368}}]}'
  red_ci='{"check_runs":[{"name":"docs-build","status":"completed","conclusion":"failure"},{"name":"ci-ok","status":"completed","conclusion":"failure","app":{"id":15368}}]}'
  pending_ci='{"check_runs":[{"name":"docs-build","status":"in_progress","conclusion":null},{"name":"ci-ok","status":"completed","conclusion":"success","app":{"id":15368}}]}'

  # reset: the default scene. A v0.10.1 release 25 h old, no open PR, the umbrella's main pinned and green.
  reset() {
    rm -rf "$t/gh/marola" "$t/gh/marola-app"
    mkdir -p "$t/gh/marola" "$t/gh/marola-app"
    printf '{"tag_name":"v0.10.1","published_at":"%s"}' "$published" >"$t/gh/latest.json"
    local s
    for s in marola marola-app; do
      echo '[]' >"$t/gh/$s/pulls.json"
      echo '[]' >"$t/gh/$s/closed.json"
      echo '{"comments":[]}' >"$t/gh/$s/comments.json"
    done
    echo 'inputs.marola-devkit.url = "github:marola-dev/marola-devkit/v0.10.1";' >"$t/gh/marola/flake.nix"
    echo "$green" >"$t/gh/marola/cr-mainsha.json"
    : >"$t/gh/calls"
    : >"$t/gh/all"
    rm -f "$t/gh/last-comment" "$t/gh/late-hold"
    retag 0.9.0 0.10.0 0.10.1
  }
  # pr <repo> <number> <variant> [<version>] [<author>] [<ref>] [<labels json>] [<head repo>] [<base>]:
  # open PR <number> at the variant's commit, with that commit as refs/pull/<number>/head and green checks.
  pr() {
    local s="$1" n="$2" sha="${head[$1-$3]}" ver="${4:-0.10.1}"
    git -C "$t/remote/marola-dev/$s" update-ref "refs/pull/$n/head" "$sha"
    jq -c --argjson n "$n" --arg sha "$sha" --arg ref "${6:-chore/devkit-v$ver}" --arg who "${5:-$BOT}" \
      --argjson labels "${7:-[]}" --arg hr "${8:-marola-dev/$s}" --arg base "${9:-main}" \
      '. + [{number: $n, head: {ref: $ref, sha: $sha, repo: {full_name: $hr}}, base: {ref: $base}, user: {login: $who}, labels: $labels}]' \
      "$t/gh/$s/pulls.json" >"$t/gh/$s/p.tmp" && mv "$t/gh/$s/p.tmp" "$t/gh/$s/pulls.json"
    echo "$green" >"$t/gh/$s/cr-$sha.json"
  }
  # go [args...]: the script, outside any repo; calls() is what it did to the fake gh, plus a non-zero exit.
  go() {
    : >"$t/gh/calls"
    rm -f "$t/gh/last-comment"
    (cd "$t" && "$SELF" "$@") >"$t/out" 2>&1 || echo "exit $?" >>"$t/gh/calls"
  }
  calls() { cat "$t/gh/calls"; }
  said() { grep -cF -- "$1" "$t/out" || true; }
  commented() { grep -cF -- "$1" "$t/gh/last-comment" 2>/dev/null || true; }

  echo "-- merges_patch_after_soak --"
  reset
  pr marola 7 0.10.1
  echo 'inputs.marola-devkit.url = "github:marola-dev/marola-devkit/v0.10.0";' >"$t/gh/marola/flake.nix"
  pr marola-app 8 0.10.1
  go --now "$after"
  ok "$(calls)" "merge marola 7 --squash --admin --match-head-commit ${head[marola-0.10.1]} -R marola-dev/marola" "the umbrella merges first and alone: the app waits for the umbrella's main"
  reset
  pr marola-app 8 0.10.1
  go --now "$after"
  ok "$(calls)" "merge marola-app 8 --squash --admin --match-head-commit ${head[marola-app-0.10.1]} -R marola-dev/marola-app" "a code repo merges once the umbrella's main is pinned and green"
  ok "$(said 'marola-dev/marola-app #8: merged')" 1 "it says so"

  echo "-- waits_during_soak --"
  reset
  pr marola 7 0.10.1
  go --now "$before"
  ok "$(calls)" "" "inside the 24 h: no merge, no comment"
  ok "$(said 'waits: soak')" 1 "it says why"
  printf '{"tag_name":"v0.10.2","published_at":"%s"}' "$(date -u -d "@$((after - 3600))" +%FT%TZ)" >"$t/gh/latest.json"
  retag 0.9.0 0.10.0 0.10.1 0.10.2
  echo '[]' >"$t/gh/marola/pulls.json"
  pr marola 7 0.10.2 0.10.2
  go --now "$after"
  ok "$(calls)" "" "a newer patch inside the soak restarts it for its own PR"
  BUMP_SOAK_HOURS=1 go --now "$after"
  ok "$(calls)" "merge marola 7 --squash --admin --match-head-commit ${head[marola-0.10.2]} -R marola-dev/marola" "BUMP_SOAK_HOURS sets the soak"

  echo "-- waits_for_canary --"
  reset
  pr marola-app 8 0.10.1
  echo 'inputs.marola-devkit.url = "github:marola-dev/marola-devkit/v0.10.0";' >"$t/gh/marola/flake.nix"
  go --now "$after"
  ok "$(calls)" "" "the umbrella's main has not pinned the tag: no merge, no comment"
  ok "$(said 'waits: ')" 1 "it says why"
  reset
  pr marola-app 8 0.10.1
  echo "$pending_ci" >"$t/gh/marola/cr-mainsha.json"
  go --now "$after"
  ok "$(calls)" "" "the umbrella's main CI is still running"
  echo '{"check_runs":[{"name":"docs-build","status":"completed","conclusion":"success"}]}' >"$t/gh/marola/cr-mainsha.json"
  go --now "$after"
  ok "$(calls)" "" "no ci-ok on the umbrella's main yet"
  echo "$red_ci" >"$t/gh/marola/cr-mainsha.json"
  go --now "$after"
  ok "$(calls)" "" "the umbrella's main is red"
  echo "$green" >"$t/gh/marola/cr-mainsha.json"
  printf '[{"merged_at":"2026-10-16T00:00:00Z","merge_commit_sha":"m1"}]' >"$t/gh/marola/closed.json"
  echo '{"check_runs":[{"name":"ci-ok","status":"completed","conclusion":"failure","app":{"id":15368}}]}' >"$t/gh/marola/cr-m1.json"
  go --now "$after"
  ok "$(calls)" "" "a failed check run on the umbrella's merge commit"
  echo '{"check_runs":[{"name":"ci-ok","status":"completed","conclusion":"cancelled","app":{"id":15368}}]}' >"$t/gh/marola/cr-m1.json"
  go --now "$after"
  ok "$(calls)" "merge marola-app 8 --squash --admin --match-head-commit ${head[marola-app-0.10.1]} -R marola-dev/marola-app" "a cancelled run (main CI cancels older ones) does not block"

  echo "-- refuses_major --"
  reset
  printf '{"tag_name":"v1.0.0","published_at":"%s"}' "$published" >"$t/gh/latest.json"
  retag 0.9.0 0.10.0 0.10.1 1.0.0
  pr marola 7 1.0.0 1.0.0
  go --now "$after"
  ok "$(calls)" "comment marola 7" "one comment, no merge"
  ok "$(commented 'waits for a person')" 1 "the comment says a person decides"

  echo "-- refuses_line_outside_pins --"
  reset
  pr marola 7 outside
  go --now "$after"
  ok "$(calls)" "comment marola 7" "one comment, no merge"
  ok "$(commented 'outside-pins: README.md')" 1 "the comment names the file"
  reset
  pr marola 7 two
  go --now "$after"
  ok "$(calls)" "comment marola 7" "two commits: one comment, no merge"
  ok "$(commented 'commits')" 1 "the comment names the rule"

  echo "-- refuses_wrong_author_or_branch --"
  reset
  pr marola 7 0.10.1 0.10.1 someone
  go --now "$after"
  ok "$(calls)" "comment marola 7" "a human author: one comment, no merge"
  reset
  pr marola 7 0.10.1 0.10.1 "$BOT" chore/devkit-v0.10.1-x
  go --now "$after"
  ok "$(calls)" "comment marola 7" "a branch that is not chore/devkit-vX.Y.Z"
  reset
  pr marola 7 0.10.1 0.10.1 "$BOT" "" "[]" someone/marola
  go --now "$after"
  ok "$(calls)" "comment marola 7" "a fork's head"
  reset
  pr marola 7 0.10.1 0.10.1 "$BOT" "" "[]" "" release
  go --now "$after"
  ok "$(calls)" "comment marola 7" "a base other than the default branch"

  echo "-- refuses_moved_head --"
  reset
  pr marola 7 0.10.1
  git -C "$t/remote/marola-dev/marola" update-ref refs/pull/7/head "${head[marola-0.10.2]}"
  go --now "$after"
  ok "$(calls)" "comment marola 7" "the PR head is not the one the checks ran on: one comment, no merge"

  echo "-- refuses_red_or_pending_check --"
  reset
  pr marola 7 0.10.1
  echo "$red_ci" >"$t/gh/marola/cr-${head[marola-0.10.1]}.json"
  go --now "$after"
  ok "$(calls)" "comment marola 7" "a red check: one comment, no merge"
  ok "$(commented docs-build)" 1 "the comment names the check"
  reset
  pr marola 7 0.10.1
  echo "$pending_ci" >"$t/gh/marola/cr-${head[marola-0.10.1]}.json"
  go --now "$after"
  ok "$(calls)" "" "a pending check waits: no merge, no comment"
  echo '{"check_runs":[{"name":"docs-build","status":"completed","conclusion":"success"}]}' >"$t/gh/marola/cr-${head[marola-0.10.1]}.json"
  go --now "$after"
  ok "$(calls)" "" "no ci-ok yet waits"
  echo "$green" >"$t/gh/marola/cr-${head[marola-0.10.1]}.json"
  echo '{"statuses":[{"context":"ext/lint","state":"failure"}]}' >"$t/gh/marola/st-${head[marola-0.10.1]}.json"
  go --now "$after"
  ok "$(calls)" "comment marola 7" "a failed commit status refuses too"

  echo "-- holds_on_label --"
  reset
  pr marola 7 0.10.1 0.10.1 "$BOT" "" '[{"name":"hold"}]'
  go --now "$after"
  ok "$(calls)" "" "a held PR: no merge, no comment"
  ok "$(said held)" 1 "it says why"

  echo "-- stops_on_kill_switch --"
  reset
  pr marola 7 0.10.1
  AUTO_MERGE=false go --now "$after"
  ok "$(calls)" "" "AUTO_MERGE=false: nothing merged, exit 0"
  ok "$(wc -c <"$t/gh/all" | tr -d ' ')" 0 "it asked gh nothing"
  ok "$(grep -c '' "$t/out")" 1 "one line"

  echo "-- one_comment_per_refusal --"
  reset
  printf '{"tag_name":"v1.0.0","published_at":"%s"}' "$published" >"$t/gh/latest.json"
  retag 0.9.0 0.10.0 0.10.1 1.0.0
  pr marola 7 1.0.0 1.0.0
  go --now "$after"
  ok "$(calls)" "comment marola 7" "the first run comments"
  go --now "$after"
  ok "$(calls)" "" "the second run does not repeat it"

  echo "-- refuses_superseded_tag --"
  reset
  pr marola 7 0.10.1 0.10.1
  printf '{"tag_name":"v0.10.2","published_at":"%s"}' "$published" >"$t/gh/latest.json"
  retag 0.9.0 0.10.0 0.10.1 0.10.2
  go --now "$after"
  ok "$(calls)" "comment marola 7" "an older tag's PR: one comment, no merge"
  ok "$(commented v0.10.2)" 1 "the comment names the newer tag"

  echo "-- waits_for_backport_release --"
  reset
  retag 0.9.0 0.10.0 0.10.1 0.10.2
  pr marola 7 0.10.1
  go --now "$after"
  ok "$(calls)" "" "the latest release is not the highest tag: no merge, no comment"
  ok "$(said 'highest v* tag')" 1 "one log line says why"

  echo "-- fails_closed_on_tag_listing --"
  reset
  printf '{"tag_name":"v1.0.0","published_at":"%s"}' "$published" >"$t/gh/latest.json"
  retag 0.9.0 0.10.0 0.10.1 1.0.0
  pr marola 7 1.0.0 1.0.0
  FAKE_LS_FAIL=1 go --now "$after"
  ok "$(calls)" "exit 1" "a failed tag listing: a red run, no merge, no comment"

  echo "-- waits_for_ci_ok_from_actions --"
  reset
  pr marola 7 0.10.1
  echo '{"check_runs":[{"name":"changes","status":"completed","conclusion":"success"},{"name":"ci-ok","status":"completed","conclusion":"success","app":{"id":99}}]}' \
    >"$t/gh/marola/cr-${head[marola-0.10.1]}.json"
  go --now "$after"
  ok "$(calls)" "" "a ci-ok from another app is no ci-ok: no merge, no comment"

  echo "-- waits_for_canary_status --"
  reset
  pr marola-app 8 0.10.1
  echo '{"statuses":[{"context":"ext/lint","state":"pending"}]}' >"$t/gh/marola/st-mainsha.json"
  go --now "$after"
  ok "$(calls)" "" "a pending status on the umbrella's head"
  echo '{"statuses":[{"context":"ext/lint","state":"failure"}]}' >"$t/gh/marola/st-mainsha.json"
  go --now "$after"
  ok "$(calls)" "" "a failed status on the umbrella's head"
  ok "$(grep -c 'contents/flake.nix$' "$t/gh/all" || true)" 0 "the pin is read at a commit, never at a branch name"

  echo "-- one_consumer_failure_does_not_stop_the_next --"
  reset
  pr marola-app 8 0.10.1
  touch "$t/gh/marola/fail-pulls"
  go --now "$after"
  ok "$(calls)" "merge marola-app 8 --squash --admin --match-head-commit ${head[marola-app-0.10.1]} -R marola-dev/marola-app"$'\nexit 1' \
    "the umbrella's failure ends red; the next repo is still judged"
  reset
  pr marola-app 8 0.10.1
  touch "$t/gh/marola/fail-cr"
  go --now "$after"
  ok "$(calls)" "exit 1" "an unreadable umbrella: the code repo does not merge"

  echo "-- fails_closed_on_canary_merge_commit_read --"
  reset
  pr marola-app 8 0.10.1
  printf '[{"merged_at":"2026-10-16T00:00:00Z","merge_commit_sha":"m1"}]' >"$t/gh/marola/closed.json"
  echo '{"check_runs":[{"name":"ci-ok","status":"completed","conclusion":"success","app":{"id":15368}}]}' >"$t/gh/marola/cr-m1.json"
  go --now "$after"
  ok "$(calls)" "merge marola-app 8 --squash --admin --match-head-commit ${head[marola-app-0.10.1]} -R marola-dev/marola-app" "control: the merge commit's runs readable and green: merges"
  touch "$t/gh/marola/fail-cr-m1"
  go --now "$after"
  ok "$(calls)" "exit 1" "the merge commit's runs unreadable: no code repo merges, the run is red"

  echo "-- unparseable_comment_read_comments_nothing --"
  reset
  pr marola 7 outside
  touch "$t/gh/marola/bad-view"
  go --now "$after"
  ok "$(calls)" "" "a comments read that is not JSON: no comment"
  ok "$(said 'not commenting')" 1 "it says so"

  echo "-- fails_closed_on_malformed_merge_commit_runs --"
  reset
  pr marola-app 8 0.10.1
  printf '[{"merged_at":"2026-10-16T00:00:00Z","merge_commit_sha":"m1"}]' >"$t/gh/marola/closed.json"
  echo '{"check_runs":["x"]}' >"$t/gh/marola/cr-m1.json"
  go --now "$after"
  ok "$(calls)" "exit 1" "runs that are not check runs: no code repo merges, the run is red"

  echo "-- bump_check_crash_is_an_error --"
  reset
  pr marola 7 0.10.1
  FAKE_PY_FAIL=1 go --now "$after"
  ok "$(calls)" "exit 1" "a crash of the content check: red, no comment, no merge"

  echo "-- fails_closed_on_bad_soak_hours --"
  reset
  pr marola 7 0.10.1
  BUMP_SOAK_HOURS=soon go --now "$after"
  ok "$(calls)" "exit 1" "a soak that is not a number: red, no merge"

  echo "-- holds_after_listing --"
  reset
  pr marola 7 0.10.1
  touch "$t/gh/late-hold"
  go --now "$after"
  ok "$(calls)" "" "a hold added after the listing: no merge, no comment"

  echo "-- failed_comment_read_comments_nothing --"
  reset
  pr marola 7 outside
  touch "$t/gh/marola/fail-view"
  go --now "$after"
  ok "$(calls)" "" "no comment when the dedupe read failed"
  ok "$(said 'not commenting')" 1 "it says so"

  echo "-- rejects_non_numeric_now --"
  reset
  pr marola 7 0.10.1
  go --now soon
  ok "$(calls)" "exit 2" "no run"

  echo "-- dry_run_changes_nothing --"
  reset
  pr marola 7 0.10.1
  go --now "$after" --dry-run
  ok "$(calls)" "" "--dry-run calls nothing"
  ok "$(said '+ gh pr merge 7 --squash --admin --match-head-commit')" 1 "it prints the merge it would run"
  reset
  pr marola 7 outside
  go --now "$after" --dry-run
  ok "$(calls)" "" "--dry-run comments nothing"
  ok "$(said '+ gh pr comment 7')" 1 "it prints the comment it would post"
  reset
  go --now "$after" --dry-run
  ok "$(said 'nothing open')" 2 "it prints a decision line per consumer"

  echo "bump-merge self-test:" "$([ "$fails" -eq 0 ] && echo ok || echo "FAILED ($fails)")"
  [ "$fails" -eq 0 ]
}

usage() { echo "usage: $0 [--dry-run] [--now EPOCH] | --self-test" >&2; exit 2; }

dry=0 now=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test; exit $? ;;
    --dry-run) dry=1 ;;
    --now) now="${2:-}"; [[ "$now" =~ ^[0-9]+$ ]] || usage; shift ;;
    *) usage ;;
  esac
  shift
done
now="${now:-$(date +%s)}"
[[ "$now" =~ ^[0-9]+$ ]] || { echo "bump-merge: no usable clock: '$now'" >&2; exit 1; }
run "$dry" "$now"
