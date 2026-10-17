#!/usr/bin/env bash
# runner-preflight — check this machine can actually run marola's workflows, before the runner
# starts taking jobs. `just runner-up` runs it and then execs the runner's own run.sh, so a
# misconfiguration is a line on your terminal at startup instead of a red job an hour later.
#
#   just runner-up                   # preflight, then start the runner
#   just runner-preflight            # the checks on their own
#   scripts/runner-preflight.sh --self-test
#
# FAIL means a workflow on this runner will break; warn means one path of one workflow will.
# Only FAIL is fatal — a warning must never stop the runner from starting, because most of what
# it warns about only bites the job that needs it.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_default() { printf '%s' "${MAROLA_REPO:-marola-dev/marola}"; }
repo="$(repo_default)"
self_test=0
[ "${1:-}" = "--self-test" ] && self_test=1

fails=0
warns=0
fail() { printf '  FAIL  %s\n' "$*" >&2; fails=$((fails + 1)); }
warn() { printf '  warn  %s\n' "$*"; warns=$((warns + 1)); }
good() { printf '  ok    %s\n' "$*"; }

# --- pure verdicts (what --self-test covers) ---------------------------------------------------

disk_verdict() {   # free_gb min_gb -> ok | low
  awk -v f="$1" -v m="$2" 'BEGIN { print (f + 0 < m + 0) ? "low" : "ok" }'
}

# --- checks ------------------------------------------------------------------------------------

check_tools() {
  # ci.yml resolves shellcheck/pyflakes/cloc/coverage through `nix build` on this runner, runs
  # every self-test with python3, lints site/static/app.js with node, and deps-merge needs jq.
  local t
  for t in nix python3 node jq curl; do
    if command -v "$t" >/dev/null; then good "$t"; else fail "$t is not on the runner's PATH"; fi
  done
  if docker info >/dev/null 2>&1; then
    good "docker (the Dockerfile and compose steps can run)"
  else
    warn "no usable docker — ci.yml's hadolint and 'docker compose config' steps will fail"
  fi
  if sudo -n true 2>/dev/null; then
    warn "passwordless sudo is available — nothing here should need it; a workflow that calls sudo is a bug, not a feature"
  else
    good "no passwordless sudo (correct — a workflow that calls it hangs until the job times out)"
  fi
}

check_runner_labels() {
  local labels
  labels="$(gh api "repos/$repo/actions/runners" --jq '[.runners[].labels[].name] | join(" ")' 2>/dev/null || true)"
  [ -n "$labels" ] || { warn "could not list $repo's self-hosted runners"; return 0; }
  case " $labels " in
    *" dependabot "*) good "a runner carries the 'dependabot' label" ;;
    *) warn "no runner carries the 'dependabot' label — harmless until 'Dependabot on self-hosted runners' is enabled, at which point update jobs queue forever" ;;
  esac
}

check_disk() {
  local work free
  work="${GHA_RUNNER_DIR:-}/_work"
  [ -d "$work" ] || work="$HOME"
  # statvfs on the directory itself, not `df` on a parent: finetune/README.md records the run where
  # `df /home` said 95 GB and statvfs on the repo said 7 TB, which is the whole answer here.
  free="$(python3 -c 'import os,sys; s=os.statvfs(sys.argv[1]); print(int(s.f_bavail*s.f_frsize/1e9))' "$work" 2>/dev/null || true)"
  [ -n "$free" ] || { warn "could not measure free disk on $work"; return 0; }
  if [ "$(disk_verdict "$free" 20)" = low ]; then
    fail "only ${free}G free on $work — a build-test run plus sbt/coursier caches will not fit"
  else
    good "${free}G free on $work"
  fi
}

self_test() {
  local f=0
  t() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FAIL $1 — got $2, want $3" >&2; f=$((f + 1)); fi; }
  t "a full disk is caught" "$(disk_verdict 3 20)" low
  t "a roomy one is not" "$(disk_verdict 900 20)" ok
  t "the boundary is not low" "$(disk_verdict 20 20)" ok
  t "MAROLA_REPO honours an override" "$(MAROLA_REPO=other/repo repo_default)" other/repo
  t "MAROLA_REPO unset falls back to the marola-dev org" "$(unset MAROLA_REPO; repo_default)" marola-dev/marola
  echo "runner-preflight self-test:" "$([ "$f" -eq 0 ] && echo ok || echo "$f FAILED")"
  [ "$f" -eq 0 ]
}

if [ "$self_test" -eq 1 ]; then self_test; exit $?; fi

echo "runner-preflight: $repo"
check_tools
check_runner_labels
check_disk
echo "runner-preflight: $fails failing, $warns warning(s)"
[ "$fails" -eq 0 ]
