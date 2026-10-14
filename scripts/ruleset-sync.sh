#!/usr/bin/env bash
# ruleset-sync — reconcile a repo's branch rulesets against .github/rulesets/*.json (one ruleset
# per file, matched on GitHub by its name; bypass-extras.json is not one), the same way
# `issues labels sync` treats .github/labels.yml (MIP-0070: the org is on GitHub Free, so
# org-level rulesets aren't available and each repo carries its own copy by hand).
#
#   ruleset-sync check [owner/repo ...]   # default: the caller's repo (gh repo view)
#   ruleset-sync apply <owner/repo ...>   # create when missing, update in place when drifted
#   ruleset-sync [--manifest FILE ...] [--extras FILE] [--consumers FILE] [--dry-run] [--all-org ORG [--include-forks]] check|apply ...
#   ruleset-sync --self-test
#
# --manifest, once or more, replaces the default set. A repo keyed in
# .github/rulesets/bypass-extras.json expects main-rule's bypass list with its extras merged by
# (actor_type, actor_id): a match overrides bypass_mode, anything else is added. The key
# "@consumers" applies to every repo in .github/consumers.txt (--consumers FILE), before that
# repo's own extras: the merge bot's bypass on every devkit consumer (MIP-0086 §5.7). No other
# ruleset takes extras. check prints "<repo> <ruleset>: ok|missing|drifted" (stderr) and a unified
# diff for a drifted one (stdout); it exits non-zero if any is missing or drifted. apply prints
# what it did (stderr) and the gh call it ran or, under --dry-run, would have run (stdout)
# instead. Never touches a ruleset whose name isn't a manifest's; no deletion command.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rulesets_dir="$root/.github/rulesets"
extras_default="$rulesets_dir/bypass-extras.json"
consumers_default="$root/.github/consumers.txt"
# bypass-extras.json predates ci-rule, which must have no bypass at all (#112).
extras_ruleset=main-rule

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# normalize_ruleset <stdin JSON> -> stdout: the manifest's own shape (pretty, sorted keys), so a
# live `gh api .../rulesets/<id>` response compares byte-for-byte against the manifest. The
# dropped fields are per-repo/per-token, not part of the ruleset's definition; left in,
# current_user_can_bypass alone would make every real repo "drifted" forever. GitHub's schema
# makes do_not_enforce_on_create and a check's integration_id optional, so absent means false and
# unset.
normalize_ruleset() {
  jq -S '
    del(.id, .node_id, ._links, .source, .source_type, .created_at, .updated_at, .current_user_can_bypass)
    | .bypass_actors |= (. // [] | sort_by(.actor_type, .actor_id))
    | .rules |= (. // [] | sort_by(.type) | map(
        if .type == "required_status_checks" then .parameters |= (
          .do_not_enforce_on_create //= false
          | .required_status_checks |= (. // [] | map(if .integration_id == null then del(.integration_id) else . end)
              | sort_by(.context, .integration_id)))
        else . end))
  '
}

# A token without admin rights gets a response with no bypass_actors; normalize_ruleset would read
# that as [] and hide a hand-added actor, so the field's absence is an error, not "no actors".
live_has_bypass() {
  jq -e 'has("bypass_actors")' <<<"$3" >/dev/null || { echo "ruleset-sync $1: $2: the response has no bypass_actors — the token cannot read them" >&2; return 1; }
}

# extras_of <repo> -> stdout: <repo>'s extras as one JSON list, the "@consumers" ones first.
extras_of() {
  local c=false
  if awk -v r="$1" '!/^[[:space:]]*(#|$)/ && $1 == r { f = 1 } END { exit !f }' "$consumers"; then c=true; fi
  jq -c --arg r "$1" --argjson c "$c" '(if $c then .["@consumers"] // [] else [] end) + (.[$r] // [])' "$extras"
}

# expected_ruleset <repo> <manifest> -> stdout: the manifest with <repo>'s extras merged in,
# reasons dropped (GitHub doesn't store them, so left in they would be drift on every check).
expected_ruleset() {
  jq --argjson x "$(extras_of "$1")" '
    .bypass_actors = reduce ($x[] | del(.reason)) as $e (.bypass_actors // [];
      if any(.[]; .actor_type == $e.actor_type and .actor_id == $e.actor_id)
      then map(if .actor_type == $e.actor_type and .actor_id == $e.actor_id then .bypass_mode = $e.bypass_mode else . end)
      else . + [$e] end)
  ' "$2"
}

# want_for <repo> <i>: sets $want (the file apply sends), $want_norm and $want_label for manifest
# <i> on <repo>. Without extras apply sends the manifest itself, byte for byte.
want_for() {
  want="${manifests[$2]}"; want_norm="${manifest_norms[$2]}"; want_label="${names[$2]} manifest"
  if [ "${names[$2]}" = "$extras_ruleset" ] && [ "$(extras_of "$1" | jq length)" -gt 0 ]; then
    want="$work/${1//\//_}.${names[$2]}.json"
    expected_ruleset "$1" "${manifests[$2]}" > "$want"
    want_norm="$(normalize_ruleset < "$want")"
    want_label="${names[$2]} manifest+extras"
  fi
}

resolve_repo() { gh repo view --json nameWithOwner -q .nameWithOwner </dev/null; }

# Forks are skipped unless asked for: an org's forks track someone else's project, and pushing
# this policy onto them by accident is the failure --all-org must not have.
org_repos() {
  gh api --paginate "orgs/$1/repos?per_page=100" </dev/null \
    --jq ".[] | select(.archived == false and ($2 or .fork == false)) | .full_name"
}

# ruleset_id <repo> <list JSON> <name> -> the id in <list> matching <name>, or empty if none.
# A non-empty result here is the only thing that lets check/apply ever touch a ruleset — anything
# else on the repo (a different name) never reaches ruleset_id's caller.
ruleset_id() {
  local ids n
  ids="$(jq -r --arg n "$3" '.[] | select(.name == $n) | .id' <<<"$2")"
  n="$(grep -c . <<<"$ids" || true)"
  [ "$n" -le 1 ] || { echo "ruleset-sync: $1 has $n rulesets named $3; resolve by hand" >&2; return 1; }
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

# check_ruleset <repo> <list JSON> <i> -> 0 when manifest <i> matches <repo>'s live ruleset.
check_ruleset() {
  local repo="$1" name="${names[$3]}" id payload repo_norm
  id="$(ruleset_id "$repo" "$2" "$name")" || return 1
  if [ -z "$id" ]; then
    echo "ruleset-sync check: $repo $name: missing" >&2
    return 1
  fi
  payload="$(gh api "repos/$repo/rulesets/$id" </dev/null)" || { echo "ruleset-sync check: $repo $name: failed to fetch ruleset $id" >&2; return 1; }
  live_has_bypass check "$repo $name" "$payload" || return 1
  repo_norm="$(normalize_ruleset <<<"$payload")"
  want_for "$repo" "$3"
  if [ "$repo_norm" = "$want_norm" ]; then
    echo "ruleset-sync check: $repo $name: ok" >&2
    return 0
  fi
  echo "ruleset-sync check: $repo $name: drifted" >&2
  diff -u --label "$want_label" --label "$repo $name" <(printf '%s\n' "$want_norm") <(printf '%s\n' "$repo_norm") || true
  return 1
}

# apply_ruleset <repo> <list JSON> <i> -> 0 on success (whatever it did or didn't do), 1 on
# failure; sets $apply_outcome to created|updated|ok|failed for the caller's tally, since an exit
# code alone can't carry "succeeded, and here is which of three things happened" under `set -e`.
apply_ruleset() {
  local repo="$1" name="${names[$3]}" id payload repo_norm
  apply_outcome=failed
  want_for "$repo" "$3"
  id="$(ruleset_id "$repo" "$2" "$name")" || return 1
  if [ -z "$id" ]; then
    echo "ruleset-sync apply: $repo $name: missing — creating" >&2
    if run gh api "repos/$repo/rulesets" --method POST --input "$want"; then apply_outcome=created; return 0; fi
    return 1
  fi
  payload="$(gh api "repos/$repo/rulesets/$id" </dev/null)" || { echo "ruleset-sync apply: $repo $name: failed to fetch ruleset $id" >&2; return 1; }
  live_has_bypass apply "$repo $name" "$payload" || return 1
  repo_norm="$(normalize_ruleset <<<"$payload")"
  if [ "$repo_norm" = "$want_norm" ]; then
    echo "ruleset-sync apply: $repo $name: ok, nothing to do" >&2
    apply_outcome=ok; return 0
  fi
  echo "ruleset-sync apply: $repo $name: drifted — updating ruleset $id" >&2
  if run gh api "repos/$repo/rulesets/$id" --method PUT --input "$want"; then apply_outcome=updated; return 0; fi
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

  local extras="$tmp/bypass-extras.json"
  cat > "$extras" <<'JSON'
{
  "@consumers": [
    { "actor_id": 8, "actor_type": "Integration", "bypass_mode": "pull_request", "reason": "synthetic: the merge bot" }
  ],
  "acme/extras-repo": [
    { "actor_id": null, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request", "reason": "synthetic: overrides the base actor's mode" },
    { "actor_id": 7, "actor_type": "Integration", "bypass_mode": "pull_request", "reason": "synthetic: an actor the base lacks" }
  ]
}
JSON

  printf '# c\nacme/consumer-repo\n' > "$tmp/consumers.txt"

  local script="${BASH_SOURCE[0]}"
  run_script() { PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" --extras "$extras" --consumers "$tmp/consumers.txt" "$@" 2>&1; }

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
  has "mixed repos: still reports the ok one" "$out" "acme/ok-repo main-rule: ok"

  echo "-- check with no repo args defaults to the caller's repo (gh repo view) --"
  jq '.id = 1' "$manifest" > "$tmp/stub/detail.acme_default-repo.1.json"
  jq -n '[{id:1,name:"main-rule"}]' > "$tmp/stub/list.acme_default-repo.json"
  out="$(STUB_NWO=acme/default-repo run_script check)" && rc=0 || rc=$?
  check "default repo: exit 0" "$rc" "0"
  has "default repo: resolved via gh repo view" "$out" "acme/default-repo main-rule: ok"

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
  has "all-org: checks the non-archived repo" "$out" "acme/ok-repo main-rule: ok"
  hasnt "all-org: skips the archived repo" "$out" "archived-repo"
  hasnt "all-org: skips a fork by default" "$out" "fork-repo"
  out="$(run_script --all-org acme --include-forks check)" && rc=0 || rc=$?
  has "all-org --include-forks: reaches the fork" "$out" "fork-repo"

  echo "-- a clean check keeps stdout empty: status and tally go to stderr --"
  out="$(PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" --extras "$extras" check acme/ok-repo 2>/dev/null)" && rc=0 || rc=$?
  check "clean check: exit 0" "$rc" "0"
  check "clean check: nothing on stdout" "$out" ""

  echo "-- two rulesets with the manifest's name is an error, not a silent pick --"
  jq -n '[{id:1,name:"main-rule"},{id:2,name:"main-rule"}]' > "$tmp/stub/list.acme_dup-repo.json"
  out="$(run_script check acme/dup-repo)" && rc=0 || rc=$?
  check "duplicate name: non-zero exit" "$([ "$rc" -ne 0 ] && echo nonzero)" "nonzero"
  has "duplicate name: says so" "$out" "2 rulesets named"

  local app='{"actor_id":7,"actor_type":"Integration","bypass_mode":"pull_request"}'
  local admin_pr='{"actor_id":null,"actor_type":"OrganizationAdmin","bypass_mode":"pull_request"}'
  jq -n '[{id:5,name:"main-rule"}]' > "$tmp/stub/list.acme_extras-repo.json"
  live_extras() { jq --argjson b "$1" '.id = 5 | .bypass_actors = $b' "$manifest" > "$tmp/stub/detail.acme_extras-repo.5.json"; }

  echo "-- check_ok_with_repo_extras: base + that repo's extras, in any order --"
  live_extras "[$app,$admin_pr]"
  out="$(run_script check acme/extras-repo)" && rc=0 || rc=$?
  check "check_ok_with_repo_extras: exit 0" "$rc" "0"
  has "check_ok_with_repo_extras: prints ok" "$out" "acme/extras-repo main-rule: ok"

  echo "-- check_drifted_when_extra_missing: the App gone, or the admin mode reverted --"
  live_extras "[$admin_pr]"
  out="$(run_script check acme/extras-repo)" && rc=0 || rc=$?
  check "check_drifted_when_extra_missing (App): exit 1" "$rc" "1"
  has "check_drifted_when_extra_missing (App): diff names it" "$out" '-      "actor_type": "Integration"'
  live_extras "[$app,{\"actor_id\":null,\"actor_type\":\"OrganizationAdmin\",\"bypass_mode\":\"always\"}]"
  out="$(run_script check acme/extras-repo)" && rc=0 || rc=$?
  check "check_drifted_when_extra_missing (admin mode): exit 1" "$rc" "1"
  has "check_drifted_when_extra_missing (admin mode): diff shows it" "$out" '+      "bypass_mode": "always"'

  echo "-- check_drifted_on_unlisted_actor: an actor in neither base nor extras --"
  live_extras "[$app,$admin_pr,{\"actor_id\":99,\"actor_type\":\"Integration\",\"bypass_mode\":\"always\"}]"
  out="$(run_script check acme/extras-repo)" && rc=0 || rc=$?
  check "check_drifted_on_unlisted_actor: exit 1" "$rc" "1"
  has "check_drifted_on_unlisted_actor: diff names it" "$out" '+      "actor_id": 99'

  echo "-- apply_keeps_extras: the PUT body is base + extras, reasons stripped --"
  live_extras "[{\"actor_id\":null,\"actor_type\":\"OrganizationAdmin\",\"bypass_mode\":\"always\"}]"
  : > "$tmp/stub/log"
  out="$(run_script apply acme/extras-repo)" && rc=0 || rc=$?
  check "apply_keeps_extras: exit 0" "$rc" "0"
  has "apply_keeps_extras: PUT to id 5" "$(cat "$tmp/stub/log")" "PUT repos/acme/extras-repo/rulesets/5"
  has "apply_keeps_extras: keeps the App" "$(cat "$tmp/stub/log")" "$app"
  has "apply_keeps_extras: keeps the admin mode" "$(cat "$tmp/stub/log")" "$admin_pr"
  hasnt "apply_keeps_extras: no admin at always" "$(cat "$tmp/stub/log")" '"bypass_mode":"always"'
  hasnt "apply_keeps_extras: reason never sent" "$(cat "$tmp/stub/log")" "reason"
  live_extras "[$app,$admin_pr]"
  : > "$tmp/stub/log"
  out="$(run_script apply acme/extras-repo)" && rc=0 || rc=$?
  check "apply_keeps_extras: already matching makes no call" "$(cat "$tmp/stub/log")" ""

  echo "-- extras_entry_needs_reason: a missing or empty reason is refused --"
  jq '.["acme/extras-repo"][1] |= del(.reason)' "$extras" > "$tmp/no-reason.json"
  jq '.["acme/extras-repo"][0].reason = " "' "$extras" > "$tmp/blank-reason.json"
  for f in no-reason blank-reason; do
    : > "$tmp/stub/log"
    out="$(PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" --extras "$tmp/$f.json" apply acme/extras-repo 2>&1)" && rc=0 || rc=$?
    check "extras_entry_needs_reason ($f): exit 1" "$rc" "1"
    has "extras_entry_needs_reason ($f): says so" "$out" "reason"
    check "extras_entry_needs_reason ($f): makes no call" "$(cat "$tmp/stub/log")" ""
  done

  local merge='{"actor_id":8,"actor_type":"Integration","bypass_mode":"pull_request"}'
  local base_admin='{"actor_id":null,"actor_type":"OrganizationAdmin","bypass_mode":"always"}'
  jq -n '[{id:6,name:"main-rule"}]' > "$tmp/stub/list.acme_consumer-repo.json"
  live_consumer() { jq --argjson b "$1" '.id = 6 | .bypass_actors = $b' "$manifest" > "$tmp/stub/detail.acme_consumer-repo.6.json"; }

  echo "-- consumer_gets_the_consumers_extra --"
  live_consumer "[$base_admin,$merge]"
  out="$(run_script check acme/consumer-repo)" && rc=0 || rc=$?
  check "consumer_gets_the_consumers_extra: exit 0" "$rc" "0"
  live_consumer "[$base_admin]"
  out="$(run_script check acme/consumer-repo)" && rc=0 || rc=$?
  check "consumer_drifted_without_it: exit 1" "$rc" "1"
  has "consumer_drifted_without_it: diff names it" "$out" '-      "actor_id": 8'
  : > "$tmp/stub/log"
  out="$(run_script apply acme/consumer-repo)" && rc=0 || rc=$?
  has "apply_adds_it_to_a_consumer" "$(cat "$tmp/stub/log")" "$merge"

  echo "-- consumer_own_extra_outranks_consumers --"
  jq '.["acme/consumer-repo"] = [{actor_id: 8, actor_type: "Integration", bypass_mode: "always", reason: "synthetic: the repo overrides the shared mode"}]' "$extras" > "$tmp/own-extras.json"
  live_consumer "[$base_admin,${merge/pull_request/always}]"
  out="$(run_script --extras "$tmp/own-extras.json" check acme/consumer-repo)" && rc=0 || rc=$?
  check "consumer_own_extra_outranks_consumers: own mode matches" "$rc" "0"

  echo "-- non_consumer_never_gets_it --"
  out="$(run_script check acme/ok-repo)" && rc=0 || rc=$?
  check "non_consumer_never_gets_it: exit 0" "$rc" "0"

  local ci="$tmp/ci-rule.json"
  cat > "$ci" <<'JSON'
{
  "bypass_actors": [],
  "conditions": { "ref_name": { "exclude": [], "include": ["~DEFAULT_BRANCH"] } },
  "enforcement": "active",
  "name": "ci-rule",
  "rules": [{
    "parameters": {
      "do_not_enforce_on_create": false,
      "required_status_checks": [{ "context": "ci-ok", "integration_id": 15368 }],
      "strict_required_status_checks_policy": false
    },
    "type": "required_status_checks"
  }],
  "target": "branch"
}
JSON
  run2() { PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" --manifest "$ci" --extras "$extras" --consumers "$tmp/consumers.txt" "$@" 2>&1; }
  live() { jq --argjson id "$3" '.id = $id' "$2" > "$tmp/stub/detail.$(tr / _ <<<"$1").$3.json"; }

  echo "-- two_rulesets_ok: main-rule and ci-rule on one repo, an unrelated one beside them --"
  jq -n '[{id:1,name:"main-rule"},{id:11,name:"ci-rule"},{id:42,name:"other-rule"}]' > "$tmp/stub/list.acme_two-repo.json"
  live acme/two-repo "$manifest" 1; live acme/two-repo "$ci" 11
  out="$(run2 check acme/two-repo)" && rc=0 || rc=$?
  check "two_rulesets_ok: exit 0" "$rc" "0"
  has "two_rulesets_ok: main-rule ok" "$out" "acme/two-repo main-rule: ok"
  has "two_rulesets_ok: ci-rule ok" "$out" "acme/two-repo ci-rule: ok"
  hasnt "two_rulesets_ok: never fetches the other ruleset" "$out" "/42"
  : > "$tmp/stub/log"
  out="$(run2 apply acme/two-repo)" && rc=0 || rc=$?
  check "two_rulesets_ok: apply makes no call" "$(cat "$tmp/stub/log")" ""

  echo "-- ci_rule_live_shape: server fields, an omitted do_not_enforce_on_create, checks in another order --"
  jq '.id = 11 | .node_id = "RRS_x" | .source = "acme/two-repo" | .source_type = "Repository"
      | .current_user_can_bypass = "never" | ._links = {self: {href: "h"}} | .created_at = "t" | .updated_at = "t"
      | .rules[0].parameters |= (del(.do_not_enforce_on_create)
          | .required_status_checks = [{context: "z-other", integration_id: null}] + .required_status_checks)' \
    "$ci" > "$tmp/stub/detail.acme_two-repo.11.json"
  jq '.rules[0].parameters.required_status_checks += [{context: "z-other"}]' "$ci" > "$tmp/ci-two-checks.json"
  out="$(PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --manifest "$manifest" --manifest "$tmp/ci-two-checks.json" --extras "$extras" --consumers "$tmp/consumers.txt" check acme/two-repo 2>&1)" && rc=0 || rc=$?
  check "ci_rule_live_shape: exit 0" "$rc" "0"
  has "ci_rule_live_shape: ci-rule ok" "$out" "acme/two-repo ci-rule: ok"
  live acme/two-repo "$ci" 11

  echo "-- ci_rule_created_when_missing: POST the ci-rule manifest as it is, main-rule untouched --"
  jq -n '[{id:3,name:"main-rule"},{id:43,name:"other-rule"}]' > "$tmp/stub/list.acme_noci-repo.json"
  live acme/noci-repo "$manifest" 3
  out="$(run2 check acme/noci-repo)" && rc=0 || rc=$?
  check "ci_rule_created_when_missing: check exit 1" "$rc" "1"
  has "ci_rule_created_when_missing: ci-rule missing" "$out" "acme/noci-repo ci-rule: missing"
  has "ci_rule_created_when_missing: main-rule still ok" "$out" "acme/noci-repo main-rule: ok"
  : > "$tmp/stub/log"
  out="$(run2 apply acme/noci-repo)" && rc=0 || rc=$?
  check "ci_rule_created_when_missing: apply exit 0" "$rc" "0"
  check "ci_rule_created_when_missing: one POST of the ci-rule manifest, nothing else" "$(cat "$tmp/stub/log")" "POST repos/acme/noci-repo/rulesets $(jq -c . "$ci")"
  has "ci_rule_created_when_missing: tally" "$out" "1 created, 0 updated, 1 ok, 0 failed"

  echo "-- ci_rule_updated_when_drifted: PUT to ci-rule's id only --"
  jq '.id = 11 | .rules[0].parameters.strict_required_status_checks_policy = true' "$ci" > "$tmp/stub/detail.acme_two-repo.11.json"
  : > "$tmp/stub/log"
  out="$(run2 apply acme/two-repo)" && rc=0 || rc=$?
  check "ci_rule_updated_when_drifted: exit 0" "$rc" "0"
  check "ci_rule_updated_when_drifted: one PUT of the ci-rule manifest to id 11" "$(cat "$tmp/stub/log")" "PUT repos/acme/two-repo/rulesets/11 $(jq -c . "$ci")"

  echo "-- ci_rule_drifted_on_bypass_actor: a hand-added actor is drift, and apply removes it --"
  jq --argjson a "$base_admin" '.id = 11 | .bypass_actors = [$a]' "$ci" > "$tmp/stub/detail.acme_two-repo.11.json"
  out="$(run2 check acme/two-repo)" && rc=0 || rc=$?
  check "ci_rule_drifted_on_bypass_actor: exit 1" "$rc" "1"
  has "ci_rule_drifted_on_bypass_actor: ci-rule drifted" "$out" "acme/two-repo ci-rule: drifted"
  has "ci_rule_drifted_on_bypass_actor: diff names it" "$out" '+      "actor_type": "OrganizationAdmin"'
  : > "$tmp/stub/log"
  out="$(run2 apply acme/two-repo)" && rc=0 || rc=$?
  has "ci_rule_drifted_on_bypass_actor: PUT sends an empty bypass list" "$(cat "$tmp/stub/log")" '"bypass_actors":[]'
  live acme/two-repo "$ci" 11

  echo "-- ci_rule_no_bypass_field: a response without bypass_actors (a token without admin) is an error --"
  jq '.id = 11 | del(.bypass_actors)' "$ci" > "$tmp/stub/detail.acme_two-repo.11.json"
  out="$(run2 check acme/two-repo)" && rc=0 || rc=$?
  check "ci_rule_no_bypass_field: exit 1" "$rc" "1"
  has "ci_rule_no_bypass_field: says why" "$out" "acme/two-repo ci-rule: the response has no bypass_actors"
  hasnt "ci_rule_no_bypass_field: not ok" "$out" "acme/two-repo ci-rule: ok"
  : > "$tmp/stub/log"
  out="$(run2 apply acme/two-repo)" && rc=0 || rc=$?
  check "ci_rule_no_bypass_field: apply exit 1" "$rc" "1"
  live acme/two-repo "$ci" 11

  echo "-- ci_rule_never_gets_extras: neither @consumers nor a repo's own --"
  for r in consumer-repo extras-repo; do
    jq -n '[]' > "$tmp/stub/list.acme_$r.json"
    : > "$tmp/stub/log"
    out="$(run2 apply "acme/$r")" && rc=0 || rc=$?
    has "ci_rule_never_gets_extras ($r): POSTs ci-rule as it is" "$(cat "$tmp/stub/log")" "POST repos/acme/$r/rulesets $(jq -c . "$ci")"
    has "ci_rule_never_gets_extras ($r): main-rule still gets its extras" "$(cat "$tmp/stub/log")" '"actor_type":"Integration"'
  done
  jq -n '[{id:6,name:"main-rule"},{id:16,name:"ci-rule"}]' > "$tmp/stub/list.acme_consumer-repo.json"
  live_consumer "[$base_admin,$merge]"; live acme/consumer-repo "$ci" 16
  out="$(run2 check acme/consumer-repo)" && rc=0 || rc=$?
  check "ci_rule_never_gets_extras: a consumer with an actor-free ci-rule is ok" "$rc" "0"

  echo "-- two_rulesets_dry_run: prints both calls, writes nothing --"
  : > "$tmp/stub/log"
  out="$(run2 --dry-run apply acme/missing-repo)" && rc=0 || rc=$?
  check "two_rulesets_dry_run: exit 0" "$rc" "0"
  check "two_rulesets_dry_run: makes no call" "$(cat "$tmp/stub/log")" ""
  has "two_rulesets_dry_run: prints the main-rule POST" "$out" "--input $manifest"
  has "two_rulesets_dry_run: prints the ci-rule POST" "$out" "--input $ci"
  has "two_rulesets_dry_run: says nothing was written" "$out" "nothing was written"

  echo "-- duplicate_manifest_names: two manifests named alike is refused --"
  out="$(run_script --manifest "$manifest" check acme/ok-repo)" && rc=0 || rc=$?
  check "duplicate_manifest_names: exit 1" "$rc" "1"
  has "duplicate_manifest_names: says so" "$out" "main-rule"

  echo "-- default_manifests: every .github/rulesets/*.json but bypass-extras.json --"
  out="$(PATH="$tmp/bin:$PATH" STUB_DIR="$tmp/stub" STUB_LOG="$tmp/stub/log" bash "$script" --consumers "$tmp/consumers.txt" --dry-run apply acme/missing-repo 2>&1)" && rc=0 || rc=$?
  check "default_manifests: exit 0" "$rc" "0"
  has "default_manifests: main-rule" "$out" "acme/missing-repo main-rule: missing"
  has "default_manifests: ci-rule" "$out" "acme/missing-repo ci-rule: missing"
  hasnt "default_manifests: not bypass-extras" "$out" "bypass-extras"

  echo "-- repo_manifests: what #112 asks of the real files --"
  local rs; rs="$(dirname "$script")/../.github/rulesets"
  check "repo_manifests: main-rule wants 1 approval" "$(jq '.rules[] | select(.type == "pull_request") | .parameters.required_approving_review_count' "$rs/main-rule.json")" "1"
  check "repo_manifests: main-rule bypass unchanged" "$(jq -c '.bypass_actors' "$rs/main-rule.json")" \
    '[{"actor_id":null,"actor_type":"OrganizationAdmin","bypass_mode":"pull_request"},{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"pull_request"}]'
  check "repo_manifests: ci-rule has an explicit empty bypass list" "$(jq -c '.bypass_actors' "$rs/ci-rule.json")" "[]"
  check "repo_manifests: ci-rule wants ci-ok from GitHub Actions, non-strict" \
    "$(jq -c '[.rules[] | {type, checks: .parameters.required_status_checks, strict: .parameters.strict_required_status_checks_policy}]' "$rs/ci-rule.json")" \
    '[{"type":"required_status_checks","checks":[{"context":"ci-ok","integration_id":15368}],"strict":false}]'

  if [ "$fails" -eq 0 ]; then echo "ruleset-sync self-test: ok"; return 0; fi
  echo "ruleset-sync self-test: $fails failure(s)" >&2; return 1
}

# --- argument parsing ---

manifests=()
extras="$extras_default"
consumers="$consumers_default"
all_org=""
include_forks=false
cmd=""
repos=()

while [ $# -gt 0 ]; do
  case "$1" in
    --manifest) manifests+=("${2:?--manifest needs a file}"); shift 2 ;;
    --extras) extras="${2:?--extras needs a file}"; shift 2 ;;
    --consumers) consumers="${2:?--consumers needs a file}"; shift 2 ;;
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

if [ "${#manifests[@]}" -eq 0 ]; then
  for f in "$rulesets_dir"/*.json; do
    [ "$f" = "$extras_default" ] || manifests+=("$f")
  done
fi
[ "${#manifests[@]}" -gt 0 ] || { echo "ruleset-sync: no manifest in $rulesets_dir" >&2; exit 1; }
names=(); manifest_norms=()
for f in "${manifests[@]}"; do
  [ -f "$f" ] || { echo "ruleset-sync: manifest not found: $f" >&2; exit 1; }
  n="$(jq -r '.name // empty' "$f")"
  [ -n "$n" ] || { echo "ruleset-sync: $f has no name" >&2; exit 1; }
  for seen in "${names[@]}"; do
    [ "$seen" != "$n" ] || { echo "ruleset-sync: two manifests are named $n" >&2; exit 1; }
  done
  names+=("$n"); manifest_norms+=("$(normalize_ruleset < "$f")")
done

[ -f "$extras" ] || { echo "ruleset-sync: extras not found: $extras" >&2; exit 1; }
[ -f "$consumers" ] || { echo "ruleset-sync: consumers not found: $consumers" >&2; exit 1; }
# A reason is what makes an extra reviewable: an exception to the shared bypass list nobody can
# explain is the one this file exists to stop.
bad_extras="$(jq -r '
  to_entries[] | .key as $r | .value[]
  | select((.reason | type) != "string" or (.reason | test("\\S") | not)
      or .actor_type == null or .bypass_mode == null or (has("actor_id") | not))
  | "  \($r): \(tojson)"
' "$extras")" || { echo "ruleset-sync: $extras: not a {\"owner/repo\": [actor, ...]} map" >&2; exit 1; }
[ -z "$bad_extras" ] || { printf 'ruleset-sync: %s: each entry needs actor_id, actor_type, bypass_mode and a non-empty reason:\n%s\n' "$extras" "$bad_extras" >&2; exit 1; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

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
ok=0; bad=0; created=0; updated=0; failed=0
for repo in "${repos[@]}"; do
  # One list per repo, every manifest matched against it.
  if ! list="$(gh api "repos/$repo/rulesets" </dev/null)"; then
    echo "ruleset-sync $cmd: $repo: failed to list rulesets" >&2
    overall_rc=1; bad=$((bad + ${#names[@]})); failed=$((failed + ${#names[@]})); continue
  fi
  for i in "${!names[@]}"; do
    case "$cmd" in
      check)
        if check_ruleset "$repo" "$list" "$i"; then ok=$((ok + 1)); else bad=$((bad + 1)); overall_rc=1; fi ;;
      apply)
        if apply_ruleset "$repo" "$list" "$i"; then :; else overall_rc=1; fi
        case "$apply_outcome" in
          created) created=$((created + 1)) ;;
          updated) updated=$((updated + 1)) ;;
          ok) ok=$((ok + 1)) ;;
          *) failed=$((failed + 1)) ;;
        esac ;;
    esac
  done
done
case "$cmd" in
  check) echo "ruleset-sync check: $ok ok, $bad missing or drifted ($((ok + bad)) rulesets in ${#repos[@]} repos)" >&2 ;;
  apply) echo "ruleset-sync apply: $created created, $updated updated, $ok ok, $failed failed ($((created + updated + ok + failed)) rulesets in ${#repos[@]} repos)$(dry_tag)" >&2 ;;
esac

exit "$overall_rc"
