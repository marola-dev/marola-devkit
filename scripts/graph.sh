#!/usr/bin/env bash
# graph — a pinned graphify, run offline and keyless, its output outside the checkout (MIP-0076 §5.2).
#
#   graph build                      extract --code-only, prune, cluster-only, name communities, into the cache
#   graph query "<question>" [...]   --budget 400 unless one is given
#   graph path <a> <b>  |  graph explain <name>
#   graph --self-test
#
# The cache is ${XDG_CACHE_HOME:-$HOME/.cache}/marola-graph/<repo>, <repo> the checkout's dir name.
set -euo pipefail

usage() { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
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
  # extract's graph: library nodes (no source_file), edges to them, a self-loop, and file nodes
  # (label = file name). cluster-only puts f, run, go in 0; alfa, beta in 1; g alone in 2.
  FAKE_GRAPH='{"nodes":[{"id":"f","label":"a.py","source_file":"a.py"},{"id":"run","label":"run()","source_file":"a.py"},{"id":"go","label":"go","source_file":"a.py"},{"id":"alfa","label":"alfa","source_file":"c.py"},{"id":"beta","label":"beta","source_file":"c.py"},{"id":"g","label":"b.py","source_file":"b.py"},{"id":"String","source_file":""},{"id":"kyo"}],"links":[{"source":"f","target":"run"},{"source":"f","target":"go"},{"source":"f","target":"g"},{"source":"run","target":"go"},{"source":"alfa","target":"beta"},{"source":"f","target":"String"},{"source":"kyo","target":"alfa"},{"source":"beta","target":"beta"}]}'
  echo '.nodes |= map(.community = ({"f":0,"run":0,"go":0,"alfa":1,"beta":1,"g":2}[.id]))' >"$tmp/assign.jq"
  # env -i strips anything that could point the fake at its log, so the path is baked in.
  cat >"$bin/graphify" <<EOF
#!$BASH
{ printf 'ARGV'; printf ' %s' "\$@"; echo; env | sed 's/^/ENV /'; } >>"$log"
o=\${GRAPHIFY_OUT:-graphify-out}; mkdir -p "\$o"
case "\$1" in
  extract) echo '$FAKE_GRAPH' >"\$o/graph.json" ;;
  cluster-only)
    echo "CLUSTERED \$(jq '.nodes | length' "\$o/graph.json") LABELS \$(jq -c . "\$o/.graphify_labels.json" 2>/dev/null)" >>"$log"
    jq -f "$tmp/assign.jq" "\$o/graph.json" >"\$o/g.tmp" && mv "\$o/g.tmp" "\$o/graph.json"
    echo 'Token cost: 0 input · 0 output' >"\$o/GRAPH_REPORT.md"; : >"\$o/graph.html" ;;
esac
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
  check "OLLAMA_BASE_URL points nowhere" "$(grep -c '^ENV OLLAMA_BASE_URL=http://127.0.0.1:9$' "$log")" "3"
  check "build runs extract --code-only --no-label, then cluster-only --no-label twice on the cached graph" \
    "$(grep '^ARGV' "$log" | tr '\n' '|')" \
    "ARGV extract . --code-only --no-label|ARGV cluster-only . --no-label --graph $cache/graph.json|ARGV cluster-only . --no-label --graph $cache/graph.json|"
  hasnot "never --backend" "$(cat "$log")" "--backend"

  echo "build_is_code_only"
  hasnot "no update call" "$(grep '^ARGV' "$log")" "ARGV update"

  echo "build_prunes_library_nodes_and_self_loops"
  check "nodes without a source_file are gone" "$(jq -c '[.nodes[].id]' "$cache/graph.json")" '["f","run","go","alfa","beta","g"]'
  check "edges touching them and self-loops are gone" "$(jq -c '[.links[] | .source + "-" + .target]' "$cache/graph.json")" \
    '["f-run","f-go","f-g","run-go","alfa-beta"]'
  check "cluster-only read the pruned graph" "$(grep -c '^CLUSTERED 6 ' "$log")" "2"

  echo "communities_named_by_hub"
  check "the first cluster-only sees no labels" "$(grep '^CLUSTERED' "$log" | head -1)" "CLUSTERED 6 LABELS "
  check "the second gets each community's hub: no file node, then shortest, then alphabetical" \
    "$(grep '^CLUSTERED' "$log" | tail -1)" 'CLUSTERED 6 LABELS {"0":"go","1":"alfa","2":"b.py"}'
  sed -i 's/^CLUSTERED/BUILT1/' "$log"; g build >/dev/null
  check "a rebuild drops the last build's labels first" "$(grep '^CLUSTERED' "$log" | head -1)" "CLUSTERED 6 LABELS "
  check "a report and HTML beside it" "$(test -f "$cache/GRAPH_REPORT.md" && test -f "$cache/graph.html" && echo y)" "y"

  echo "no_auto_refresh_set"
  check "every build call has GRAPHIFY_NO_AUTO_REFRESH=1" "$(grep -c '^ENV GRAPHIFY_NO_AUTO_REFRESH=1$' "$log")" \
    "$(grep -c '^ARGV' "$log")"

  echo "refuses_update_and_extract"
  : >"$log"
  rc=0; out=$(g extract . --code-only --no-label) || rc=$?
  check "extract exits non-zero" "$((rc != 0))" "1"
  has "and points at graph build" "$out" "graph build"
  rc=0; out=$(g update .) || rc=$?
  check "update exits non-zero" "$((rc != 0))" "1"
  has "and points at graph build" "$out" "graph build"
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
  git init -q "$tmp/plain" && git -C "$tmp/plain" commit -q --allow-empty -m p
  out=$(cd "$tmp/plain" && bash "$self" build >/dev/null 2>&1; bash "$self" query where 2>&1 || true)
  hasnot "no submodules, fresh graph: no staleness line" "$out" "stale"
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
subs() { git -C "$TOP" submodule status | awk 'NF {print $1, $2}'; }

stamp() {
  jq -n --arg head "$(git -C "$TOP" rev-parse HEAD)" --arg submodules "$(git -C "$TOP" submodule status)" \
    '{head: $head, submodules: $submodules}'
}

# Library and package nodes (no source_file: `String`, `kyo`) are hubs that bridge unrelated code,
# and self-loops are noise; both crowd real symbols out of a query's budget (#82).
prune() {
  jq '(reduce (.nodes[] | select((.source_file // "") != "") | .id) as $i ({}; .[$i] = true)) as $keep
    | .nodes |= map(select($keep[.id]))
    | .links |= map(select($keep[.source] and $keep[.target] and .source != .target))' \
    "$OUT/graph.json" >"$OUT/graph.json.tmp"
  mv "$OUT/graph.json.tmp" "$OUT/graph.json"
}

# Each community after its highest-degree member, a file node only when nothing else is there;
# ties go to the shortest label, then the first alphabetically.
name_communities() {
  jq '(reduce .links[] as $l ({}; .[$l.source] += 1 | .[$l.target] += 1)) as $deg
    | [.nodes[] | select(.community != null)
       | {c: .community, l: ((.label // .id) | sub("\\(\\)$"; "")), d: ($deg[.id] // 0),
          file: (.label == ((.source_file // "") | split("/") | last))}]
    | group_by(.c)
    | map(([.[] | select(.file | not)] | if length > 0 then . else null end) // .
          | sort_by(-.d, (.l | length), .l) | first | {key: (.c | tostring), value: .l})
    | from_entries' "$OUT/graph.json" >"$OUT/.graphify_labels.json"
}

# No `update`: its Markdown pass filled the graph with section headings (#82). The first cluster-only
# assigns communities; given a labels file and no .sig, the second keeps those names when the
# community count matches and hub-names the rest itself. --no-label: no model call either way.
build() {
  local missing
  missing=$(git -C "$TOP" submodule status | awk '/^-/ {print $2}' | tr '\n' ' ')
  [ -z "$missing" ] || die "uninitialised submodule(s): ${missing% }; run git submodule update --init"
  mkdir -p "$OUT"
  rm -f "$OUT/.graphify_labels.json" "$OUT/.graphify_labels.json.sig"
  (cd "$TOP" && run extract . --code-only --no-label)
  prune
  (cd "$TOP" && run cluster-only . --no-label --graph "$OUT/graph.json")
  name_communities
  (cd "$TOP" && run cluster-only . --no-label --graph "$OUT/graph.json")
  # Trap: with named labels present, cluster-only first copies the graph to a dated backup dir.
  rm -rf "$OUT"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]
  stamp >"$OUT/build.json"
  echo "graph: built $OUT/graph.json"
}

stale_notice() {
  local f="$OUT/build.json"
  [ -f "$f" ] || die "no graph for $(basename "$TOP"); run: graph build"
  if [ "$(jq -r .head "$f")" != "$(git -C "$TOP" rev-parse HEAD)" ] ||
     [ "$(jq -r .submodules "$f" | awk 'NF {print $1, $2}')" != "$(subs)" ]; then
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
  extract|update) die "$verb would overwrite the pruned graph; run: graph build" ;;
  *) die "unknown command '$verb' (build, query, path, explain)" ;;
esac
