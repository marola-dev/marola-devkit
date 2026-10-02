#!/usr/bin/env bash
# ruleset-sync — reconcile a repo's branch ruleset against .github/rulesets/main-rule.json, the
# same way `issues labels sync` treats .github/labels.yml (MIP-0070: the org is on GitHub Free,
# so org-level rulesets aren't available and each repo carries its own copy by hand).
#
#   ruleset-sync check [owner/repo ...]   # default: the caller's repo (gh repo view)
#   ruleset-sync apply <owner/repo ...>   # create when missing, update in place when drifted
#   ruleset-sync [--manifest FILE] [--dry-run] [--all-org ORG [--include-forks]] check|apply ...
#   ruleset-sync --self-test
#
# check prints ok/missing/drifted per repo (stderr) and a unified diff for a drifted one
# (stdout); it exits non-zero if any repo is missing or drifted. apply prints what it did
# (stderr) and the gh call it ran or, under --dry-run, would have run (stdout) instead. Never
# touches a ruleset whose name isn't the manifest's; no deletion command.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest_default="$root/.github/rulesets/main-rule.json"

usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# normalize_ruleset <stdin JSON> -> stdout: the manifest's own shape (pretty, sorted keys), so a
# live `gh api .../rulesets/<id>` response compares byte-for-byte against the manifest. The
# dropped fields are per-repo/per-token, not part of the ruleset's definition; left in,
# current_user_can_bypass alone would make every real repo "drifted" forever.
normalize_ruleset() {
  jq -S '
    del(.id, .node_id, ._links, .source, .source_type, .created_at, .updated_at, .current_user_can_bypass)
    | .bypass_actors |= (. // [] | sort_by(.actor_type))
    | .rules |= (. // [] | sort_by(.type))
  '
}

resolve_repo() { gh repo view --json nameWithOwner -q .nameWithOwner </dev/null; }

# Forks are skipped unless asked for: an org's forks track someone else's project, and pushing
# this policy onto them by accident is the failure --all-org must not have.
org_repos() {
  gh api --paginate "orgs/$1/repos?per_page=100" </dev/null \
    --jq ".[] | select(.archived == false and ($2 or .fork == false)) | .full_name"
}

# ruleset_id <repo> <name> -> that repo's ruleset id matching <name>, or empty if none exists.
# A non-empty result here is the only thing that lets check/apply ever touch a ruleset — anything
# else on the repo (a different name) never reaches ruleset_id's caller.
ruleset_id() {
  local list
  list="$(gh api "repos/$1/rulesets" </dev/null)" || return 1
  local ids n
  ids="$(jq -r --arg n "$2" '.[] | select(.name == $n) | .id' <<<"$list")"
  n="$(grep -c . <<<"$ids" || true)"
  [ "$n" -le 1 ] || { echo "ruleset-sync: $1 has $n rulesets named $2; resolve by hand" >&2; return 1; }
  printf '%s' "$ids"
}

dry=0
dry_tag() { [ "$dry" -eq 0 ] || printf ' (--dry-run: nothing was written)'; }

# Mirrors issues.sh's run(): dry prints the call instead of making it; a real call echoes what it
# ran on success, or "FAILED (exit N): ..." on stderr, so the caller's $? still reflects it.
run() {
  if [ "$dry" -eq 1 ]; then printf '+'; printf ' %q' "$@"; printf '\n'; return 0; fi
  local rc=0
  "$@" </dev/null >/dev/null || rc=$?
  if [ "$rc" -eq 0 ]; then { printf ' %q' "$@"; printf '\n'; }
  else { printf 'FAILED (exit %d):' "$rc"; printf ' %q' "$@"; printf '\n'; } >&2; fi
  return "$rc"
}

check_repo() {
  local repo="$1" id payload repo_norm
  id="$(ruleset_id "$repo" "$ruleset_name")" || { echo "ruleset-sync check: $repo: failed to list rulesets" >&2; return 1; }
  if [ -z "$id" ]; then
    echo "ruleset-sync check: $repo: missing" >&2
    return 1
  fi
  payload="$(gh api "repos/$repo/rulesets/$id" </dev/null)" || { echo "ruleset-sync check: $repo: failed to fetch ruleset $id" >&2; return 1; }
  repo_norm="$(normalize_ruleset <<<"$payload")"
  if [ "$repo_norm" = "$manifest_norm" ]; then
    echo "ruleset-sync check: $repo: ok" >&2
    return 0
  fi
  echo "ruleset-sync check: $repo: drifted" >&2
  diff -u --label manifest --label "$repo" <(printf '%s\n' "$manifest_norm") <(printf '%s\n' "$repo_norm") || true
  return 1
}

# apply_repo <repo> -> 0 on success (whatever it did or didn't do), 1 on failure; sets
# $apply_outcome to created|updated|ok|failed for the caller's tally, since an exit code alone
# can't carry "succeeded, and here is which of three things happened" under `set -e`.
apply_repo() {
  local repo="$1" id payload repo_norm
  apply_outcome=failed
  id="$(ruleset_id "$repo" "$ruleset_name")" || { echo "ruleset-sync apply: $repo: failed to list rulesets" >&2; return 1; }
  if [ -z "$id" ]; then
    echo "ruleset-sync apply: $repo: missing — creating" >&2
    if run gh api "repos/$repo/rulesets" --method POST --input "$manifest"; then apply_outcome=created; return 0; fi
    return 1
  fi
  payload="$(gh api "repos/$repo/rulesets/$id" </dev/null)" || { echo "ruleset-sync apply: $repo: failed to fetch ruleset $id" >&2; return 1; }
  repo_norm="$(normalize_ruleset <<<"$payload")"
  if [ "$repo_norm" = "$manifest_norm" ]; then
    echo "ruleset-sync apply: $repo: ok, nothing to do" >&2
    apply_outcome=ok; return 0
  fi
  echo "ruleset-sync apply: $repo: drifted — updating ruleset $id" >&2
  if run gh api "repos/$repo/rulesets/$id" --method PUT --input "$manifest"; then apply_outcome=updated; return 0; fi
  return 1
}

# --- self-test ---

write_gh_stub() {
  mkdir -p "$1/bin"
  { echo "#!$BASH"; cat <<'STUB'
if [ "$1" = repo ] && [ "$2" = view ]; then printf '%s\n' "${STUB_NWO:-acme/default-repo}"; exit 0; fi
[ "$1" = api ] || exit 1
shift
method=GET; input=""; jq_expr=""; path=""; prev=""
for a in "$@"; do
  case "$prev" in --method) method="$a" ;; --input) input="$a" ;; --jq) jq_expr="$a" ;; esac
  case "$a" in repos/*|orgs/*) path="$a" ;; esac
  prev="$a"
done
log() { [ -z "${STUB_LOG:-}" ] || printf '%s\n' "$*" >> "$STUB_LOG"; }
slug() { printf '%s' "$1" | tr '/' '_'; }
case "$path" in
  orgs/*/repos*)
    org="${path#orgs/}"; org="${org%%/repos*}"
    file="$STUB_DIR/org-repos.$org.json"; [ -f "$file" ] || file="$STUB_DIR/org-repos.json"
    if [ -n "$jq_expr" ]; then jq -r "$jq_expr" "$file"; else cat "$file"; fi
    exit 0 ;;
  repos/*/rulesets)
    repo="${path#repos/}"; repo="${repo%/rulesets}"
    if [ "$method" = POST ]; then log "POST $path $(jq -c . "$input")"; echo '{"id":999}'; exit 0; fi
    file="$STUB_DIR/list.$(slug "$repo").json"; [ -f "$file" ] && cat "$file" || echo '[]'
    exit 0 ;;
  repos/*/rulesets/*)
    rest="${path#repos/}"; repo="${rest%/rulesets/*}"; id="${rest##*/}"
    if [ "$method" = PUT ]; then log "PUT $path $(jq -c . "$input")"; exit 0; fi
    file="$STUB_DIR/detail.$(slug "$repo").$id.json"
    if [ -f "$file" ]; then cat "$file"; exit 0; fi
    echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;
  *) exit 1 ;;
esac
STUB
  } > "$1/bin/gh"
  chmod +x "$1/bin/gh"
}

self_test() {
  local tmp fails=0 out rc
  check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else fails=$((fails + 1)); echo "  FAIL $1 — got [$2] want [$3]" >&2; fi; }
  has() { case "$2" in *"$3"*) echo "  ok   $1" ;; *) fails=$((fails + 1)); echo "  FAIL $1 — expected \"$3\" in: $2" >&2 ;; esac; }
  hasnt() { case "$2" in *"$3"*) fails=$((fails + 1)); echo "  FAIL $1 — did not expect \"$3\" in: $2" >&2 ;; *) echo "  ok   $1" ;; esac; }

  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  mkdir -p "$tmp/stub"
  write_gh_stub "$tmp"

  local manifest="$tmp/main-rule.json"
  cat > "$manifest" <<'JSON'
{
  "bypass_actors": [
    { "actor_id": null, "actor_type": "OrganizationAdmin", "bypass_mode": "always" }
  ],
  "conditions": { "ref_name": { "exclude": [], "include": ["~DEFAULT_BRANCH"] } },
  "enforcement": "active",
  "name": "main-rule",
  "rules": [{ "type": "deletion" }, { "type": "non_fast_forward" }],
  "target": "branch"
}
JSON

  local script="${BASH_SOURCE[0]}"
  run_script() { PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" "$@" 2>&1; }

  echo "-- an identical ruleset, alongside an unrelated one on the same repo --"
  jq '.id = 1' "$manifest" > "$tmp/stub/detail.acme_ok-repo.1.json"
  jq -n '[{id:1,name:"main-rule"},{id:42,name:"other-rule"}]' > "$tmp/stub/list.acme_ok-repo.json"
  out="$(run_script check acme/ok-repo)" && rc=0 || rc=$?
  check "ok repo: exit 0" "$rc" "0"
  has "ok repo: prints ok" "$out" "ok"
  hasnt "ok repo: never fetches the other ruleset" "$out" "/42"

  echo "-- a repo with no main-rule ruleset --"
  jq -n '[]' > "$tmp/stub/list.acme_missing-repo.json"
  out="$(run_script check acme/missing-repo)" && rc=0 || rc=$?
  check "missing repo: exit 1" "$rc" "1"
  has "missing repo: prints missing" "$out" "missing"

  echo "-- a drifted ruleset (enforcement differs), alongside an unrelated one --"
  jq '.id = 2 | .enforcement = "evaluate"' "$manifest" > "$tmp/stub/detail.acme_drift-repo.2.json"
  jq -n '[{id:2,name:"main-rule"},{id:77,name:"other-rule"}]' > "$tmp/stub/list.acme_drift-repo.json"
  out="$(run_script check acme/drift-repo)" && rc=0 || rc=$?
  check "drifted repo: exit 1" "$rc" "1"
  has "drifted repo: prints a unified diff" "$out" '-  "enforcement": "active"'

  echo "-- check over several repos: exit non-zero if any fails, but still reports each --"
  out="$(run_script check acme/ok-repo acme/missing-repo)" && rc=0 || rc=$?
  check "mixed repos: exit 1" "$rc" "1"
  has "mixed repos: still reports the ok one" "$out" "acme/ok-repo: ok"

  echo "-- check with no repo args defaults to the caller's repo (gh repo view) --"
  jq '.id = 1' "$manifest" > "$tmp/stub/detail.acme_default-repo.1.json"
  jq -n '[{id:1,name:"main-rule"}]' > "$tmp/stub/list.acme_default-repo.json"
  out="$(STUB_NWO=acme/default-repo run_script check)" && rc=0 || rc=$?
  check "default repo: exit 0" "$rc" "0"
  has "default repo: resolved via gh repo view" "$out" "acme/default-repo: ok"

  echo "-- apply on a missing repo: POST with the manifest body --"
  : > "$tmp/stub/log"
  out="$(run_script apply acme/missing-repo)" && rc=0 || rc=$?
  check "apply create: exit 0" "$rc" "0"
  has "apply create: logs a POST" "$(cat "$tmp/stub/log")" "POST repos/acme/missing-repo/rulesets"
  has "apply create: body is the manifest" "$(cat "$tmp/stub/log")" "$(jq -c . "$manifest")"

  echo "-- apply on a drifted repo: PUT to the right id, the other ruleset untouched --"
  : > "$tmp/stub/log"
  out="$(run_script apply acme/drift-repo)" && rc=0 || rc=$?
  check "apply update: exit 0" "$rc" "0"
  has "apply update: logs a PUT to id 2" "$(cat "$tmp/stub/log")" "PUT repos/acme/drift-repo/rulesets/2"
  has "apply update: body is the manifest" "$(cat "$tmp/stub/log")" "$(jq -c . "$manifest")"
  hasnt "apply update: never touches id 77" "$(cat "$tmp/stub/log")" "/77"

  echo "-- apply on an ok repo: no call at all --"
  : > "$tmp/stub/log"
  out="$(run_script apply acme/ok-repo)" && rc=0 || rc=$?
  check "apply ok: exit 0" "$rc" "0"
  check "apply ok: makes no call" "$(cat "$tmp/stub/log")" ""
  has "apply ok: says nothing to do" "$out" "nothing to do"

  echo "-- --dry-run makes no call and prints the gh call it would have made --"
  : > "$tmp/stub/log"
  out="$(run_script --dry-run apply acme/missing-repo)" && rc=0 || rc=$?
  check "dry-run: exit 0" "$rc" "0"
  check "dry-run: makes no call" "$(cat "$tmp/stub/log")" ""
  has "dry-run: prints the gh call" "$out" "gh api repos/acme/missing-repo/rulesets --method POST --input"

  echo "-- --all-org lists only the org's own non-archived repos --"
  jq -n '[{full_name:"acme/ok-repo",archived:false,fork:false},{full_name:"acme/archived-repo",archived:true,fork:false},{full_name:"acme/fork-repo",archived:false,fork:true}]' \
    > "$tmp/stub/org-repos.acme.json"
  out="$(run_script --all-org acme check)" && rc=0 || rc=$?
  check "all-org: exit 0 (only the non-archived repo checked)" "$rc" "0"
  has "all-org: checks the non-archived repo" "$out" "acme/ok-repo: ok"
  hasnt "all-org: skips the archived repo" "$out" "archived-repo"
  hasnt "all-org: skips a fork by default" "$out" "fork-repo"
  out="$(run_script --all-org acme --include-forks check)" && rc=0 || rc=$?
  has "all-org --include-forks: reaches the fork" "$out" "fork-repo"

  echo "-- a clean check keeps stdout empty: status and tally go to stderr --"
  out="$(PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" check acme/ok-repo 2>/dev/null)" && rc=0 || rc=$?
  check "clean check: exit 0" "$rc" "0"
  check "clean check: nothing on stdout" "$out" ""

  echo "-- two rulesets with the manifest's name is an error, not a silent pick --"
  jq -n '[{id:1,name:"main-rule"},{id:2,name:"main-rule"}]' > "$tmp/stub/list.acme_dup-repo.json"
  out="$(run_script check acme/dup-repo)" && rc=0 || rc=$?
  check "duplicate name: non-zero exit" "$([ "$rc" -ne 0 ] && echo nonzero)" "nonzero"
  has "duplicate name: says so" "$out" "2 rulesets named"

  if [ "$fails" -eq 0 ]; then echo "ruleset-sync self-test: ok"; return 0; fi
  echo "ruleset-sync self-test: $fails failure(s)" >&2; return 1
}

# --- argument parsing ---

manifest="$manifest_default"
all_org=""
include_forks=false
cmd=""
repos=()

while [ $# -gt 0 ]; do
  case "$1" in
    --manifest) manifest="${2:?--manifest needs a file}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --all-org) all_org="${2:?--all-org needs an org}"; shift 2 ;;
    --include-forks) include_forks=true; shift ;;
    --self-test) self_test; exit $? ;;
    -h|--help) usage; exit 0 ;;
    check|apply) [ -z "$cmd" ] || { echo "ruleset-sync: command given twice" >&2; exit 1; }; cmd="$1"; shift ;;
    -*) echo "ruleset-sync: unknown argument: $1" >&2; usage >&2; exit 1 ;;
    *) repos+=("$1"); shift ;;
  esac
done

[ -n "$cmd" ] || { echo "ruleset-sync: expects check or apply" >&2; usage >&2; exit 1; }
[ -z "$all_org" ] || [ "${#repos[@]}" -eq 0 ] || { echo "ruleset-sync: --all-org and explicit repos are mutually exclusive" >&2; exit 1; }

[ -f "$manifest" ] || { echo "ruleset-sync: manifest not found: $manifest" >&2; exit 1; }
ruleset_name="$(jq -r .name "$manifest")"
manifest_norm="$(normalize_ruleset < "$manifest")"

if [ -n "$all_org" ]; then
  mapfile -t repos < <(org_repos "$all_org" "$include_forks")
fi

if [ "${#repos[@]}" -eq 0 ]; then
  case "$cmd" in
    check) repos=("$(resolve_repo)") ;;
    apply) echo "ruleset-sync apply: no repos given (and --all-org not set)" >&2; exit 1 ;;
  esac
fi

overall_rc=0
case "$cmd" in
  check)
    ok=0; bad=0
    for repo in "${repos[@]}"; do
      if check_repo "$repo"; then ok=$((ok + 1)); else bad=$((bad + 1)); overall_rc=1; fi
    done
    echo "ruleset-sync check: $ok ok, $bad missing or drifted ($((ok + bad)) repos checked)" >&2
    ;;
  apply)
    created=0; updated=0; ok=0; failed=0
    for repo in "${repos[@]}"; do
      if apply_repo "$repo"; then :; else overall_rc=1; fi
      case "$apply_outcome" in
        created) created=$((created + 1)) ;;
        updated) updated=$((updated + 1)) ;;
        ok) ok=$((ok + 1)) ;;
        *) failed=$((failed + 1)) ;;
      esac
    done
    echo "ruleset-sync apply: $created created, $updated updated, $ok ok, $failed failed ($((created + updated + ok + failed)) repos)$(dry_tag)" >&2
    ;;
esac

exit "$overall_rc"
