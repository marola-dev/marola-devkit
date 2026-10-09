#!/usr/bin/env bash
# graph — a pinned graphify, run offline and keyless, its output outside the checkout (MIP-0076 §5.2).
#
#   graph build                      extract --code-only --no-label, then update, into the cache
#   graph query "<question>" [...]   --budget 400 unless one is given
#   graph path <a> <b>  |  graph explain <name>
#   graph extract|update . [...]     passed through; extract needs --code-only
#   graph --self-test
#
# The cache is ${XDG_CACHE_HOME:-$HOME/.cache}/marola-graph/<repo>, <repo> the checkout's dir name.
set -euo pipefail

usage() { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { echo "graph: $*" >&2; exit 1; }

self_test() {
  local tmp fails=0 bin log repo sub cache out rc self
  self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always \
    GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
  check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else fails=$((fails + 1)); echo "  FAIL $1 — got [$2] want [$3]" >&2; fi; }
  has() { case "$2" in *"$3"*) echo "  ok   $1" ;; *) fails=$((fails + 1)); echo "  FAIL $1 — expected \"$3\" in: $2" >&2 ;; esac; }
  hasnot() { case "$2" in *"$3"*) fails=$((fails + 1)); echo "  FAIL $1 — did not expect \"$3\" in: $2" >&2 ;; *) echo "  ok   $1" ;; esac; }

  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  bin="$tmp/bin" log="$tmp/graphify.log" repo="$tmp/r" sub="$tmp/s"
  mkdir -p "$bin" && : >"$log"
  # env -i strips anything that could point the fake at its log, so the path is baked in.
  cat >"$bin/graphify" <<EOF
#!/usr/bin/env bash
{ printf 'ARGV'; printf ' %s' "\$@"; echo; env | sed 's/^/ENV /'; } >>"$log"
o=\${GRAPHIFY_OUT:-graphify-out}; mkdir -p "\$o"; echo '{}' >"\$o/graph.json"
EOF
  chmod +x "$bin/graphify"
  export PATH="$bin:$PATH" XDG_CACHE_HOME="$tmp/cache"
  export ANTHROPIC_API_KEY=leak OPENAI_API_KEY=leak GEMINI_API_KEY=leak OPENAI_BASE_URL=leak ANTHROPIC_MODEL=leak
  cache="$XDG_CACHE_HOME/marola-graph/r"

  git init -q "$sub" && git -C "$sub" commit -q --allow-empty -m s
  git init -q "$repo" && echo 'x = 1' >"$repo/a.py"
  git -C "$repo" submodule -q add "$sub" s 2>/dev/null
  git -C "$repo" add -A && git -C "$repo" commit -q -m init
  g() { (cd "$repo" && bash "$self" "$@" 2>&1); }

  echo "env_scrubbed"
  rc=0; out=$(g build) || rc=$?
  check "build exits 0" "$rc" "0"
  hasnot "no *_API_KEY reaches graphify" "$(grep '^ENV ' "$log" || true)" "_API_KEY="
  hasnot "no ANTHROPIC_* reaches graphify" "$(grep '^ENV ' "$log" || true)" "ENV ANTHROPIC_"
  hasnot "no OPENAI_* reaches graphify" "$(grep '^ENV ' "$log" || true)" "ENV OPENAI_"
  check "only §5.2's five variables (bash adds PWD, SHLVL, _)" \
    "$(grep '^ENV ' "$log" | sed 's/^ENV //; s/=.*//' | grep -vxE 'PWD|SHLVL|_|OLDPWD' | sort -u | tr '\n' ' ')" \
    "GRAPHIFY_NO_AUTO_REFRESH GRAPHIFY_OUT HOME OLLAMA_BASE_URL PATH "
  check "OLLAMA_BASE_URL points nowhere" "$(grep -c '^ENV OLLAMA_BASE_URL=http://127.0.0.1:9$' "$log")" "2"
  check "build runs extract --code-only --no-label, then update" "$(grep '^ARGV' "$log" | tr '\n' '|')" \
    "ARGV extract . --code-only --no-label|ARGV update .|"
  hasnot "never --backend" "$(cat "$log")" "--backend"

  echo "no_auto_refresh_set"
  check "both build calls have GRAPHIFY_NO_AUTO_REFRESH=1" "$(grep -c '^ENV GRAPHIFY_NO_AUTO_REFRESH=1$' "$log")" "2"

  echo "refuses_extract_without_code_only"
  : >"$log"
  rc=0; out=$(g extract .) || rc=$?
  check "extract without --code-only exits non-zero" "$((rc != 0))" "1"
  has "and says why" "$out" "--code-only"
  rc=0; out=$(g query q --backend claude) || rc=$?
  check "--backend is refused too" "$((rc != 0))" "1"
  check "graphify was never called" "$(wc -c <"$log" | tr -d ' ')" "0"

  echo "output_outside_checkout"
  check "git status --porcelain --ignored is empty after a build" "$(git -C "$repo" status --porcelain --ignored)" ""
  check "graph.json is in the cache" "$(test -f "$cache/graph.json" && echo y)" "y"
  check "build.json records HEAD" "$(jq -r .head "$cache/build.json" 2>/dev/null)" "$(git -C "$repo" rev-parse HEAD)"
  check "build.json records the submodule status" "$(jq -r .submodules "$cache/build.json" 2>/dev/null)" \
    "$(git -C "$repo" submodule status)"

  echo "stale_notice_on_new_head"
  out=$(g query where || true)
  hasnot "fresh graph: no staleness line" "$out" "stale"
  git -C "$repo" commit -q --allow-empty -m next
  out=$(g query where || true)
  has "new HEAD: a staleness line" "$out" "stale"
  g build >/dev/null || true
  git -C "$repo/s" commit -q --allow-empty -m s2
  out=$(g explain thing || true)
  has "moved submodule: a staleness line" "$out" "stale"

  echo "query_default_budget_400"
  : >"$log"
  g query "where is x" >/dev/null || true
  check "query defaults to --budget 400 and reads the cached graph" "$(grep '^ARGV' "$log")" \
    "ARGV query where is x --graph $cache/graph.json --budget 400"
  : >"$log"
  g query q --budget 900 >/dev/null || true
  check "an explicit --budget wins" "$(grep '^ARGV' "$log")" "ARGV query q --budget 900 --graph $cache/graph.json"

  echo "uninitialised_submodule_fails"
  git -C "$repo" submodule -q deinit -f s
  : >"$log"
  rc=0; out=$(g build) || rc=$?
  check "build exits non-zero" "$((rc != 0))" "1"
  has "and names the submodule" "$out" "uninitialised submodule(s): s"
  check "graphify was never called" "$(wc -c <"$log" | tr -d ' ')" "0"

  if [ "$fails" -eq 0 ]; then echo "graph self-test: ok"; return 0; fi
  echo "graph self-test: $fails failure(s)" >&2; return 1
}

# Every graphify call: no key, no backend, no network where a user namespace is allowed.
# PATH and HOME (the cache dir) go through; --code-only and --no-label are the real guarantee.
run() {
  local -a cmd=(env -i PATH="$PATH" HOME="$OUT" GRAPHIFY_OUT="$OUT" GRAPHIFY_NO_AUTO_REFRESH=1
    OLLAMA_BASE_URL=http://127.0.0.1:9 graphify "$@")
  if [ -z "${NETNS:-}" ]; then
    NETNS=no
    if command -v unshare >/dev/null && unshare -rn true 2>/dev/null; then NETNS=yes
    else echo "graph: unshare -rn not permitted here; running without network isolation" >&2; fi
  fi
  if [ "$NETNS" = yes ]; then unshare -rn "${cmd[@]}"; else "${cmd[@]}"; fi
}

# `+` (checked out off the recorded commit) counts; the trailing describe text does not.
subs() { git -C "$TOP" submodule status | awk '{print $1, $2}'; }

stamp() {
  jq -n --arg head "$(git -C "$TOP" rev-parse HEAD)" --arg submodules "$(git -C "$TOP" submodule status)" \
    '{head: $head, submodules: $submodules}'
}

build() {
  local missing
  missing=$(git -C "$TOP" submodule status | awk '/^-/ {print $2}' | tr '\n' ' ')
  [ -z "$missing" ] || die "uninitialised submodule(s): ${missing% }; run git submodule update --init"
  mkdir -p "$OUT"
  (cd "$TOP" && run extract . --code-only --no-label && run update .)
  stamp >"$OUT/build.json"
  echo "graph: built $OUT/graph.json"
}

stale_notice() {
  local f="$OUT/build.json"
  [ -f "$f" ] || die "no graph for $(basename "$TOP"); run: graph build"
  if [ "$(jq -r .head "$f")" != "$(git -C "$TOP" rev-parse HEAD)" ] ||
     [ "$(jq -r .submodules "$f" | awk '{print $1, $2}')" != "$(subs)" ]; then
    echo "graph: stale — HEAD or a submodule moved since the build; run: graph build (about 3 s)" >&2
  fi
}

case "${1:-}" in
  --self-test) self_test; exit $? ;;
  -h|--help|"") usage; exit 0 ;;
esac
for a in "$@"; do [ "${a%%=*}" != --backend ] || die "--backend is never passed: no model call on this path"; done
TOP=$(git rev-parse --show-toplevel) || die "not in a git checkout"
OUT="${XDG_CACHE_HOME:-$HOME/.cache}/marola-graph/$(basename "$TOP")"
verb=$1; shift
case "$verb" in
  build) build ;;
  query)
    stale_notice
    budget=(--budget 400)
    for a in "$@"; do case "$a" in --budget|--budget=*) budget=() ;; esac; done
    run query "$@" --graph "$OUT/graph.json" "${budget[@]}" ;;
  path|explain) stale_notice; run "$verb" "$@" --graph "$OUT/graph.json" ;;
  extract|update)
    [ "$verb" = update ] || [[ " $* " == *" --code-only "* ]] || die "extract needs --code-only (without it graphify calls a model)"
    [ "$verb" = update ] || [[ " $* " == *" --no-label "* ]] || set -- "$@" --no-label
    mkdir -p "$OUT"; run "$verb" "$@" ;;
  *) die "unknown command '$verb' (build, query, path, explain, extract, update)" ;;
esac
