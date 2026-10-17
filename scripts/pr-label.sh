#!/usr/bin/env bash
# pr-label — apply the deterministic label taxonomy (scripts/lib/pr_labels.sh) to one PR.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pr_labels.sh.
source "$script_dir/lib/pr_labels.sh"

dry_run=0; pr_arg=""; self_test=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --self-test) self_test=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    [0-9]*|\#[0-9]*) pr_arg="${arg#\#}" ;;
    *) echo "pr-label: unrecognized argument: $arg" >&2; exit 1 ;;
  esac
done

self_test() {
  local fails=0 got
  check() {   # label, got, want
    if [ "$2" = "$3" ]; then echo "  ok   $1"; else fails=$((fails + 1)); echo "  FAIL $1 — got '$2', want '$3'" >&2; fi
  }
  got="$(pr_label_kind app/marola-pr-bot update/sbt-2.0.10 "project/build.properties")"
  check "a marola-pr-bot update/ PR is kind/deps" "$got" "kind/deps"
  got="$(pr_label_kind app/marola-pr-bot ml/compiled-prompt-1 "prompts/compiled.json")"
  check "a marola-pr-bot PR on another branch is not" "$got" ""
  got="$(pr_label_kind app/dependabot dependabot/pip/x "requirements.txt")"
  check "a dependabot PR is kind/deps" "$got" "kind/deps"
  got="$(pr_label_classify update/sbt-2.0.10 app/marola-pr-bot "Update sbt to 2.0.10" "project/build.properties" | grep '^kind/' || true)"
  check "pr_label_classify passes the branch on" "$got" "kind/deps"
  echo "pr-label self-test:" "$([ "$fails" -eq 0 ] && echo ok || echo "$fails FAILED")"
  [ "$fails" -eq 0 ]
}
if [ "$self_test" -eq 1 ]; then self_test; exit $?; fi

if [ -z "$pr_arg" ]; then
  pr_arg="$(gh pr view --json number --jq .number 2>/dev/null)" || {
    echo "pr-label: no PR number given and no PR found for the current branch" >&2
    exit 1
  }
fi

json="$(gh pr view "$pr_arg" --json number,url,headRefName,author,commits,files,labels)"
number="$(jq -r .number <<<"$json")"
url="$(jq -r .url <<<"$json")"
ref="$(jq -r .headRefName <<<"$json")"
author="$(jq -r .author.login <<<"$json")"
subjects="$(jq -r '.commits[].messageHeadline' <<<"$json")"
paths="$(jq -r '.files[].path' <<<"$json")"
existing="$(jq -r '.labels[].name' <<<"$json")"

mapfile -t new_labels < <(pr_label_classify "$ref" "$author" "$subjects" "$paths")

to_add=()
for l in "${new_labels[@]}"; do
  grep -qxF "$l" <<<"$existing" || to_add+=("$l")
done

if [ "${#to_add[@]}" -eq 0 ]; then
  echo "pr-label: #$number already has every label this run would add — nothing to do ($url)"
  exit 0
fi

echo "pr-label: #$number ($url) -> ${to_add[*]}"
[ "$dry_run" -eq 1 ] && { echo "pr-label: --dry-run, not applying"; exit 0; }

ensure_pr_labels
joined="$(IFS=,; echo "${to_add[*]}")"
gh pr edit "$number" --add-label "$joined" >/dev/null
echo "pr-label: labeled #$number"
