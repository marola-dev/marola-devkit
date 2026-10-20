#!/usr/bin/env bash
# deps-merge — merge every open dependency-update PR whose checks are green, in one command.
#
#   just deps-merge                # merge what is ready, report what is not and why
#   just deps-merge --dry-run      # print the gh commands, mutate nothing
#   just deps-merge --from-json f  # judge this file instead of calling gh (testing)
#   just deps-merge --self-test    # scripts/fixtures/deps-merge-*.json, assert the verdicts
#   deps-merge --repo OWNER/REPO --unattended   # the scheduled job's mode, stricter rules below
#
# The counterpart to deps-stack.sh: that one chains open dependency PRs into a stack when they
# arrive separately, this one closes them out. A PR is merged only when GitHub says MERGEABLE and
# every check has finished green — SKIPPED and NEUTRAL count as green because ci.yml's path
# filters skip most jobs on a requirements-only change, and a skipped job is not a failed one.
# Everything else is reported and left alone; nothing here overrides a red check.
#
# Order is deps-stack's: github-actions first, then pip, each by PR number ascending, so a run
# that merges several leaves the smallest rebase for whatever is left.
#
# --unattended (marola-dev/marola#740, MIP-0086 §5.5) also requires: Dependabot as author of the PR
# and of every commit, no `hold` label, only Python manifests changed, and every changed manifest
# line the same dependency with only its versions moved, none by a major (on 0.x, a minor counts as
# major). Versions come from the diff, not the PR text: Dependabot's pip range updates carry no
# update-type. The merge pins the head commit the checks ran on. The caller checks AUTO_MERGE.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dry=0
from_json=""
self_test=0
unattended=false
repo=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --from-json) from_json="${2:?--from-json needs a file}"; shift ;;
    --self-test) self_test=1 ;;
    --unattended) unattended=true ;;
    --repo) repo="${2:?--repo needs OWNER/REPO}"; shift ;;
    -h|--help) sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "deps-merge: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
if [ "$unattended" = true ] && [ -z "$repo" ] && [ "$self_test" -eq 0 ]; then
  echo "deps-merge: --unattended needs --repo OWNER/REPO" >&2; exit 2
fi
repo_args=()
[ -z "$repo" ] || repo_args=(-R "$repo")

# $1 true|false overrides --unattended. stdin: gh-pr-list-shaped JSON -> stdout: same, ordered,
# each with .ecosystem/.ready/.reason
judge() {
  jq --argjson unattended "${1:-$unattended}" '
    def eco:
      if (.headRefName | test("^dependabot/github_actions/")) then "github-actions"
      elif (.headRefName | test("^dependabot/pip/")) then "pip"
      elif (.headRefName | test("^update/")) then "scala-steward"
      else "other" end;
    def rank:
      if eco == "github-actions" then 0 elif eco == "pip" then 1
      elif eco == "scala-steward" then 2 else 3 end;
    # The rollup keeps every run of a check, so a run that concurrency cancelled stays in it after
    # a newer run of the same check passed (marola-ml#90). Drop only such a CANCELLED entry: an
    # older FAILURE still counts, so a newer run that skips its jobs cannot hide it. Newer = the
    # higher job id in detailsUrl (a re-run gets a new one), then the later startedAt.
    def check_key: if .name then ["run", (.workflowName // ""), .name]
      elif .context then ["status", .context] else null end;
    def check_order: [((.detailsUrl // "") | (capture("/job/(?<j>[0-9]+)") | .j | tonumber) // 0),
      (.startedAt // "")];
    def checks: (.statusCheckRollup // []) as $all
      | $all | map(. as $c | ($c | check_key) as $k | select(
          ((($c.conclusion // "") | ascii_upcase) != "CANCELLED") or $k == null
          or ([$all[] | select(check_key == $k and check_order > ($c | check_order))] | length) == 0));
    # A CheckRun reports status+conclusion, a StatusContext only state — read both shapes.
    def results: checks | map(.conclusion // .state // "PENDING" | ascii_upcase);
    def running: checks | map(select((.status // "COMPLETED") != "COMPLETED")) | length;
    # $r, not `.`: inside the pipe `.` is the literal array, so `index(.)` would test the array
    # against itself and never match — which is how the first version merged a red PR.
    def red: results | map(select(. as $r | ["SUCCESS", "SKIPPED", "NEUTRAL"] | index($r) | not));
    def verdict:
      if .mergeable != "MERGEABLE" then "not mergeable (\(.mergeable))"
      elif (checks | length) == 0 then "no checks reported"
      elif running > 0 then "checks still running"
      elif (red | length) > 0 then "checks not green (\(red | unique | join(", ")))"
      else "" end;
    def manifest: test("(^|/)(requirements[^/]*\\.txt|pyproject\\.toml)$");
    def lockfile: test("(^|/)(uv|poetry|pdm|Pipfile)\\.lock$");
    def ver_re: "[0-9]+(?:\\.[0-9]+)*";
    # A requirements line or a quoted pyproject entry; `version = "1.2"` has no operator, so is not one.
    def dep_line:
      .[0:1] as $sign | .[1:] | sub("^\\s+"; "") | sub("^[\"\u0027]"; "") | sub("[\"\u0027],?\\s*$"; "")
      | ([capture("^(?<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?<rest>\\s*(\\[[^]]*\\])?\\s*(===|==|>=|<=|~=|!=|<|>).*)$")] | first)
      | if . == null then null
        else {sign: $sign, name: (.name | ascii_downcase | gsub("[-_.]+"; "-")), rest: .rest} end;
    def parts: split(".") | map(tonumber);
    def is_major($o; $n): ($o | parts) as $a | ($n | parts) as $b
      | $a[0] != $b[0] or ($a[0] == 0 and ($a[1] // 0) != ($b[1] // 0));
    def pair_verdict:
      (map(select(.sign == "-"))[0].rest) as $o | (map(select(.sign == "+"))[0].rest) as $n
      | ($o | [scan(ver_re)]) as $ov | ($n | [scan(ver_re)]) as $nv
      | if ($o | gsub(ver_re; "V")) != ($n | gsub(ver_re; "V")) then "changes more than a version (\(.[0].name))"
        elif any(range(0; $ov | length); is_major($ov[.]; $nv[.])) then
          "a major update (\(.[0].name): \($o | ltrimstr(" ")) -> \($n | ltrimstr(" ")))"
        else "" end;
    def versions_verdict:
      [.files[] | select(.path | manifest) | .patch | split("\n")[] | select(test("^[-+]")) | dep_line] as $ls
      | ($ls | map(select(. != null)) | group_by(.name)) as $groups
      | if any($ls[]; . == null) then "changes a manifest line that is not a dependency"
        elif ($groups | length) == 0 then "no dependency version change to judge"
        else ([$groups[] | select((map(.sign) | sort) != ["+", "-"]) | .[0].name] | first) as $odd
          | if $odd != null then "adds or removes a dependency (\($odd))"
            else ([$groups[] | pair_verdict | select(. != "")] | first) // "" end
        end;
    def unattended_verdict:
      ([.files[]?.path | select((manifest or lockfile) | not)] | first) as $stray
      | ([.files[]? | select((.path | manifest) and .patch == null) | .path] | first) as $nodiff
      | if (.author.login // "") as $a | ["app/dependabot", "dependabot[bot]"] | index($a) | not
        then "author is not Dependabot (\(.author.login // "unknown"))"
        elif any(.labels[]?; .name == "hold") then "labelled hold"
        elif (.commits // []) | length == 0 then "no commits reported"
        elif any(.commits[].authors[]?; .login != "dependabot[bot]") then "has a commit not by Dependabot"
        elif (.files // []) | length == 0 then "no changed files reported"
        elif $stray != null then "changes a file outside the Python manifests (\($stray))"
        elif $nodiff != null then "no diff for \($nodiff)"
        elif any(.commits[]; (.messageBody // "") | test("update-type: version-update:semver-major"))
        then "Dependabot marks a major update"
        else versions_verdict end;
    map(. + {ecosystem: eco, rank: rank,
             reason: (if $unattended then (verdict as $v | unattended_verdict | if . == "" then $v else . end)
                      else verdict end)})
    | map(. + {ready: (.reason == "")})
    | sort_by([.rank, .number])
  '
}

# stdin: gh-pr-list-shaped JSON -> stdout: same, each UNKNOWN mergeable read again. GitHub computes
# mergeable only when asked, so the first read of a PR nobody has looked at says UNKNOWN
# (marola-ml#90); a read a few seconds later has the answer. Still UNKNOWN after the tries: refused.
settle() {
  local prs n m try
  prs="$(cat)"
  for n in $(jq -r '.[] | select(.mergeable == "UNKNOWN") | .number' <<<"$prs"); do
    m=UNKNOWN
    for try in 1 2 3 4 5; do
      sleep "${DEPS_MERGE_RETRY_SLEEP:-3}"
      m="$(gh pr view "$n" "${repo_args[@]}" --json mergeable -q .mergeable || echo UNKNOWN)"
      [ "$m" = UNKNOWN ] || break
    done
    prs="$(jq --argjson n "$n" --arg m "$m" 'map(if .number == $n then .mergeable = $m else . end)' <<<"$prs")"
  done
  printf '%s\n' "$prs"
}

discover() {
  if [ -n "$from_json" ]; then
    cat "$from_json"
    return 0
  fi
  gh auth status >/dev/null 2>&1 || { echo "gh is not logged in — run: gh auth login" >&2; exit 1; }
  if [ "$unattended" = false ]; then
    gh pr list "${repo_args[@]}" --state open --search "author:app/dependabot" \
      --json number,title,headRefName,mergeable,statusCheckRollup | settle
    return 0
  fi
  local pr n files
  gh pr list -R "$repo" --state open --search "author:app/dependabot" \
    --json number,title,headRefName,headRefOid,mergeable,statusCheckRollup,author,labels,commits \
    | settle | jq -c '.[]' | while read -r pr; do
      n="$(jq -r .number <<<"$pr")"
      # gh pr list has the paths but not the patches the version rule reads.
      files="$(gh api --paginate "repos/$repo/pulls/$n/files" -q '.[] | {path: .filename, patch}' | jq -s .)"
      jq --argjson files "$files" '. + {files: $files}' <<<"$pr"
    done | jq -s .
}

self_test() {
  local fixture="$script_dir/fixtures/deps-merge-prs.json"
  [ -f "$fixture" ] || { echo "deps-merge self-test: fixture not found: $fixture" >&2; exit 1; }
  local judged got fails=0
  judged="$(judge <"$fixture")"

  check() {   # label, got, want
    if [ "$2" = "$3" ]; then
      echo "  ok   $1"
    else
      fails=$((fails + 1))
      echo "  FAIL $1 — got $2, want $3" >&2
    fi
  }

  got="$(jq -c '[.[].number]' <<<"$judged")"
  check "github-actions first, then pip, each by number" "$got" "[301,303,302,304,305]"
  got="$(jq -c '[.[] | select(.ready) | .number]' <<<"$judged")"
  check "only the green, mergeable PRs are merged" "$got" "[301,302]"
  got="$(jq -r '.[] | select(.number == 303) | .reason' <<<"$judged")"
  check "a failed check is reported, not merged" "$got" "checks not green (FAILURE)"
  got="$(jq -r '.[] | select(.number == 304) | .reason' <<<"$judged")"
  check "a still-running check waits" "$got" "checks still running"
  got="$(jq -r '.[] | select(.number == 305) | .reason' <<<"$judged")"
  check "a conflicting PR is left for a human" "$got" "not mergeable (CONFLICTING)"
  got="$(jq -r '.[] | select(.number == 302) | .ready' <<<"$judged")"
  check "SKIPPED jobs count as green (ci.yml's path filters skip most)" "$got" "true"

  fixture="$script_dir/fixtures/deps-merge-unattended.json"
  got="$(judge <"$fixture" | jq -c '[.[] | select(.ready) | .number]')"
  check "interactive mode ignores the unattended rules" "$got" "[401,402,403,404,407,408,409,410,411,412,413,414,415]"
  judged="$(judge true <"$fixture")"
  got="$(jq -c '[.[] | select(.ready) | .number]' <<<"$judged")"
  check "unattended: only green patch/minor manifest-only Dependabot PRs merge" "$got" "[401,412,415]"
  while IFS='|' read -r n want; do
    got="$(jq -r --argjson n "$n" '.[] | select(.number == $n) | .reason' <<<"$judged")"
    check "unattended refuses #$n" "$got" "$want"
  done <<'EOF'
402|a major update (dspy: >=3.4.1,<4 -> >=4.0.0,<5)
403|author is not Dependabot (app/marola-pr-bot)
404|changes a file outside the Python manifests (.github/workflows/compile-prompt.yml)
405|checks not green (FAILURE)
406|checks still running
407|labelled hold
408|has a commit not by Dependabot
409|adds or removes a dependency (evil-pkg)
410|a major update (peft: >=0.21.2 -> >=0.22.0)
411|Dependabot marks a major update
413|a major update (mlflow: >=3.17.1,<4 -> >=3.17.1,<5)
414|changes a manifest line that is not a dependency
416|checks not green (FAILURE)
EOF
  local out err
  err="$(mktemp)"
  out="$(bash "${BASH_SOURCE[0]}" --unattended --repo o/r --from-json "$fixture" --dry-run 2>"$err")"
  got="$(grep -c '^+ gh pr merge' <<<"$out")"
  check "unattended dry run merges three" "$got" "3"
  got="$(grep -c -- '+ gh pr merge 401 -R o/r --squash --delete-branch --admin --match-head-commit 401aaaa$' <<<"$out")"
  check "the merge pins the head commit the checks ran on, past the review rule" "$got" "1"
  out="$(bash "${BASH_SOURCE[0]}" --from-json "$fixture" --dry-run 2>/dev/null)"
  got="$(grep -c -- '--admin' <<<"$out" || true)"
  check "a person's run never passes --admin" "$got" "0"
  got="$(grep -c '^deps-merge: #[0-9]* refused: ' "$err")"
  check "one stderr line per refused PR" "$got" "13"
  rm -f "$err"

  # A stub gh: pr list says UNKNOWN for #401, pr view says MERGEABLE from read $STUB_AFTER on.
  local stub
  stub="$(mktemp -d)"
  jq '[.[] | select(.number == 401) | .mergeable = "UNKNOWN" | del(.files)]' "$fixture" >"$stub/list.json"
  jq -c '.[] | select(.number == 401) | .files[]' "$fixture" >"$stub/files.jsonl"
  printf '#!%s\n' "$BASH" >"$stub/gh"
  cat >>"$stub/gh" <<'STUB'
d="$(dirname "$0")"
case "$1" in
  auth) exit 0 ;;
  api) cat "$d/files.jsonl" ;;
  pr) case "$2" in
        list) cat "$d/list.json" ;;
        view) c=$(( $(cat "$d/views" 2>/dev/null || echo 0) + 1 )); echo "$c" >"$d/views"
              if [ "$c" -ge "$STUB_AFTER" ]; then echo MERGEABLE; else echo UNKNOWN; fi ;;
      esac ;;
esac
STUB
  chmod +x "$stub/gh"
  out="$(PATH="$stub:$PATH" STUB_AFTER=2 DEPS_MERGE_RETRY_SLEEP=0 \
    bash "${BASH_SOURCE[0]}" --unattended --repo o/r --dry-run 2>&1)"
  got="$(grep -c -- '^+ gh pr merge 401 ' <<<"$out")/$(cat "$stub/views" 2>/dev/null || echo 0)"
  check "an UNKNOWN mergeable is read again, then merged (merges/reads)" "$got" "1/2"
  rm -f "$stub/views"
  out="$(PATH="$stub:$PATH" STUB_AFTER=99 DEPS_MERGE_RETRY_SLEEP=0 \
    bash "${BASH_SOURCE[0]}" --unattended --repo o/r --dry-run 2>&1)"
  got="$(grep -c '^deps-merge: #401 refused: not mergeable (UNKNOWN)$' <<<"$out")/$(cat "$stub/views" 2>/dev/null || echo 0)"
  check "still UNKNOWN after 5 reads: refused (refusals/reads)" "$got" "1/5"
  rm -rf "$stub"

  echo "deps-merge self-test:" "$([ "$fails" -eq 0 ] && echo ok || echo "$fails FAILED")"
  [ "$fails" -eq 0 ]
}

if [ "$self_test" -eq 1 ]; then self_test; exit $?; fi

judged="$(discover | judge)"
if [ "$(jq 'length' <<<"$judged")" -eq 0 ]; then
  echo "deps-merge: no open dependency-update PRs"
  exit 0
fi

if [ "$unattended" = true ]; then
  jq -r '.[] | select(.ready) | "  #\(.number) READY  \(.title)"' <<<"$judged"
  jq -r '.[] | select(.ready | not) | "deps-merge: #\(.number) refused: \(.reason)"' <<<"$judged" >&2
else
  jq -r '.[] | "  #\(.number) \(.ecosystem)  \(if .ready then "READY" else "skip — \(.reason)" end)\n         \(.title)"' <<<"$judged"
fi

merged=0
failed=0
while read -r n oid; do
  [ -n "$n" ] || continue
  cmd=(gh pr merge "$n" "${repo_args[@]}" --squash --delete-branch)
  # Unattended, the merge bot is a pull-request-only bypass actor of the repo's main ruleset, and
  # these rules stand in for the review it requires. --admin only skips gh's client-side refusal of
  # a BLOCKED PR, as in bump-merge.sh; GitHub still decides (marola-ml#90 failed without it). A
  # person's run never passes it, so their merge still needs the review.
  [ "$unattended" = false ] || cmd+=(--admin --match-head-commit "$oid")
  if [ "$dry" -eq 1 ]; then
    echo "+ ${cmd[*]}"
    continue
  fi
  echo "merging #$n ..."
  if "${cmd[@]}"; then
    merged=$((merged + 1))
  else
    failed=$((failed + 1))
    echo "deps-merge: #$n did not merge — left open" >&2
  fi
done < <(jq -r '.[] | select(.ready) | "\(.number) \(.headRefOid // "")"' <<<"$judged")

[ "$dry" -eq 1 ] && { echo "dry run: nothing was merged"; exit 0; }
echo "deps-merge: merged $merged, left $(jq '[.[] | select(.ready | not)] | length' <<<"$judged") not ready, $failed failed"
[ "$failed" -eq 0 ]
