#!/usr/bin/env bash
# bump-consumers: after a devkit release, one PR per repo in .github/consumers.txt on
# chore/devkit-vX.Y.Z, in two steps so the merge bot's key never shares a job with nix (MIP-0086
# §5.6). prepare needs no token: it clones each repo, moves its pins (release.py --consumer), its
# flake.lock's devkit node and REPOS.md's wiring block (BUMP_WIRING overrides the new tag's
# `nix run …#wiring`), commits, and writes DIR/version and, per bumped repo,
# DIR/<owner>_<repo>.patch, .base and (with a flake.lock) .lock. publish needs
# GH_TOKEN on every repo: it applies each patch to a fresh clone as the git identity it runs under,
# checks the patch's content first (bump_check.py), force-pushes the branch and opens or edits the
# PR. One repo failing or refused doesn't stop the others.
#   scripts/bump-consumers.sh prepare X.Y.Z DIR
#   scripts/bump-consumers.sh publish X.Y.Z DIR
#   scripts/bump-consumers.sh names      # the consumers as repo names, comma-separated (bot-token's input)
#   scripts/bump-consumers.sh repos      # the consumers as owner/repo lines (BUMP_CONSUMERS_FILE overrides the list)
#   scripts/bump-consumers.sh tag-rev X.Y.Z   # the commit vX.Y.Z points at in BUMP_DEVKIT_URL; exit 1 if none
#   scripts/bump-consumers.sh --self-test
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

repos() { grep -Ev '^[[:space:]]*(#|$)' "${1:-${BUMP_CONSUMERS_FILE:-$root/.github/consumers.txt}}"; }

# tag_rev vX.Y.Z: the commit the devkit tag points at. Exact ref names: ls-remote patterns match a
# ref's tail, so refs/heads/x/refs/tags/vX would answer too. bump-merge.sh judges against the same one.
tag_rev() {
  local tag="$1" refs rev
  refs="$(git ls-remote "${BUMP_DEVKIT_URL:-https://github.com/marola-dev/marola-devkit}" "refs/tags/$tag" "refs/tags/$tag^{}")" || return 1
  rev="$(awk -v r="refs/tags/$tag^{}" '$2 == r { print $1; exit }' <<<"$refs")"
  [ -n "$rev" ] || rev="$(awk -v r="refs/tags/$tag" '$2 == r { print $1; exit }' <<<"$refs")"
  [ -n "$rev" ] || return 1
  printf '%s\n' "$rev"
}

if [ "${1:-}" = --self-test ]; then
  bad="$(repos | grep -Ev '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' || true)"
  [ -z "$bad" ] || { echo "bump-consumers: not owner/repo: $bad" >&2; exit 1; }
  [ "$(repos | sort | uniq -d)" = "" ] || { echo "bump-consumers: a repo is listed twice" >&2; exit 1; }
  printf '# c\n\na/b\n' >"${TMPDIR:-/tmp}/consumers.$$"
  [ "$(repos "${TMPDIR:-/tmp}/consumers.$$")" = a/b ] || { echo "bump-consumers: comment or blank line read as a repo" >&2; exit 1; }
  rm -f "${TMPDIR:-/tmp}/consumers.$$"

  # Hermetic bumps of fixture repos: file:// remotes, a fake gh, a fake wiring that writes the
  # submodule's checked-out commit into the block (so it proves the submodules were initialised).
  t="$(mktemp -d)"; trap 'rm -rf "$t"' EXIT
  fails=0
  ok() { if [ "$1" = "$2" ]; then echo "ok   $3"; else echo "FAIL $3: got '$1', want '$2'"; fails=$((fails + 1)); fi; }
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  # git >= 2.38.1 refuses file:// submodules unless told otherwise.
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always
  mkdir -p "$t/bin" "$t/remote/a"
  printf '#!%s\necho "gh $*" >>"%s/gh.log"\n' "$BASH" "$t" >"$t/bin/gh"
  cat >"$t/bin/wiring" <<EOF
#!$BASH
set -euo pipefail
echo "\$*" >>"$t/wiring.log"
[ -z "\${WIRING_FAIL:-}" ] || exit 3
[ -e sub/.git ] || { echo "sub is not checked out" >&2; exit 4; }
sed "s/^sub at .*/sub at \$(git -C sub rev-parse HEAD)/" "\$1" >"\$1.new"
mv "\$1.new" "\$1"
EOF
  # Fake devkit remote with an annotated tag, and a nix that rewrites the lock's devkit node the way
  # `nix flake update marola-devkit` would.
  git init -q -b main "$t/devkit"
  git -C "$t/devkit" commit -q --allow-empty -m devkit
  git -C "$t/devkit" tag -a -m v9.9.9 v9.9.9
  cat >"$t/nixlock.py" <<'PY'
import json, sys
path, rev = sys.argv[1:]
lock = json.load(open(path))
node = lock["nodes"]["marola-devkit"]
node["original"]["ref"] = "v9.9.9"
node["locked"].update(rev=rev, narHash="sha256-new", lastModified=2)
json.dump(lock, open(path, "w"), indent=2)
PY
  cat >"$t/bin/nix" <<EOF
#!$BASH
echo "nix \$*" >>"$t/nix.log"
if [ "\$*" = "flake update marola-devkit" ] && [ -f flake.lock ]; then
  python3 "$t/nixlock.py" flake.lock "\$(git -C "$t/devkit" rev-parse 'v9.9.9^{commit}')"
fi
EOF
  chmod +x "$t/bin/gh" "$t/bin/wiring" "$t/bin/nix"
  git init -q -b main "$t/sub"
  git -C "$t/sub" commit -q --allow-empty -m sub
  fixture() {   # fixture NAME MARKERS(0|1) [PINNED_VERSION]; FX_WORKFLOW=1 adds a devkit workflow, FX_LOCK=1 a flake.lock
    local d="$t/src/$1"
    git init -q -b main "$d"
    mkdir -p "$d/docs/2-Building-marola"
    echo "inputs.marola-devkit.url = \"github:marola-dev/marola-devkit/v${3:-0.0.1}\";" >"$d/flake.nix"
    if [ -n "${FX_WORKFLOW:-}" ]; then
      mkdir -p "$d/.github/workflows"
      printf 'jobs:\n  a:\n    uses: marola-dev/marola-devkit/.github/workflows/x.yml@v%s\n  b:\n    runs-on: x\n    steps:\n      - run: echo hi\n' \
        "${3:-0.0.1}" >"$d/.github/workflows/ci.yml"
    fi
    if [ -n "${FX_LOCK:-}" ]; then
      printf '{"nodes":{"root":{"inputs":{"marola-devkit":"marola-devkit","nixpkgs":"nixpkgs"}},"marola-devkit":{"locked":{"lastModified":1,"narHash":"sha256-old","owner":"marola-dev","repo":"marola-devkit","rev":"%s","type":"github"},"original":{"owner":"marola-dev","ref":"v%s","repo":"marola-devkit","type":"github"}},"nixpkgs":{"locked":{"owner":"NixOS","repo":"nixpkgs","rev":"%s","type":"github"}}},"root":"root","version":7}\n' \
        "$(printf 'a%.0s' {1..40})" "${3:-0.0.1}" "$(printf 'b%.0s' {1..40})" >"$d/flake.lock"
    fi
    git -C "$d" submodule add -q "file://$t/sub" sub
    if [ "$2" = 1 ]; then
      printf '# Repos\n<!-- wiring:start -->\nsub at stale\n<!-- wiring:end -->\n' >"$d/docs/2-Building-marola/REPOS.md"
    else
      printf '# Repos\nsub at stale\n' >"$d/docs/2-Building-marola/REPOS.md"
    fi
    git -C "$d" add -A
    git -C "$d" commit -q -m fixture
    git clone -q --bare "$d" "$t/remote/a/$1"
  }
  prep() {   # prep REPO [ENV=VALUE...]: exit status echoed
    local repo="$1" rc=0; shift
    env PATH="$t/bin:$PATH" BUMP_CONSUMERS_URL="file://$t/remote" BUMP_WIRING=wiring "$@" \
      bash "$root/scripts/bump-consumers.sh" --one prepare 9.9.9 "$repo" "$t/out" >/dev/null 2>"$t/err" || rc=$?
    echo "$rc"
  }
  pub() {
    local repo="$1" rc=0; shift
    env PATH="$t/bin:$PATH" BUMP_CONSUMERS_URL="file://$t/remote" BUMP_DEVKIT_URL="file://$t/devkit" GIT_AUTHOR_NAME=bot GIT_COMMITTER_NAME=bot "$@" \
      bash "$root/scripts/bump-consumers.sh" --one publish 9.9.9 "$repo" "$t/out" >/dev/null 2>>"$t/err" || rc=$?
    echo "$rc"
  }
  run() { local rc; rc="$(prep "$@")"; [ "$rc" != 0 ] || rc="$(pub "$1")"; echo "$rc"; }
  mkdir -p "$t/out"
  tamper() {   # tamper REPO CMD...: rewrite the prepared patch after CMD edits the patched clone
    local w="$t/tamper" slug="${1//\//_}"
    git clone -q --branch main "file://$t/remote/$1" "$w"
    ( cd "$w" && git switch -q -c x && git am -q --keep-cr "$t/out/$slug.patch" && "${@:2}" \
      && git add -A && git commit -q --amend --no-edit && git format-patch --binary --stdout origin/main..HEAD >"$t/out/$slug.patch" )
    rm -rf "$w"
  }
  inject_step() { printf '      - run: echo injected\n' >>.github/workflows/ci.yml; }
  lock_edit() {   # lock_edit NODE: set that node's locked.rev to a made-up SHA
    python3 - "$1" <<'PY'
import json, sys
lock = json.load(open("flake.lock"))
lock["nodes"][sys.argv[1]]["locked"]["rev"] = "c" * 40
json.dump(lock, open("flake.lock", "w"), indent=2)
PY
  }
  lock_host() {
    python3 - <<'PY'
import json
lock = json.load(open("flake.lock"))
lock["nodes"]["marola-devkit"]["locked"]["host"] = "evil.example.com"
json.dump(lock, open("flake.lock", "w"), indent=2)
PY
  }
  second_commit() { echo x >extra && git add extra && git commit -q -m second; }
  edit_outside() { sed -i 's/^# Repos$/# Repos edited/' docs/2-Building-marola/REPOS.md; }
  # refused REPO RULE CMD...: tamper, publish; nothing pushed, no gh call, one line naming repo and rule
  refused() {
    local rc
    rc="$(prep "$1")"; ok "$rc" 0 "prepare succeeds"
    tamper "$1" "${@:3}"
    : >"$t/err"; rm -f "$t/gh.log"
    rc="$(pub "$1")"
    ok "$([ "$rc" != 0 ] && echo refused)" refused "publish fails"
    ok "$(files "$1")" "" "nothing pushed"
    ok "$(cat "$t/gh.log" 2>/dev/null || echo never)" never "no gh call"
    ok "$(grep -c '' "$t/err")" 1 "one error line"
    ok "$(grep -c "^bump-consumers: $1: refused, $2: " "$t/err")" 1 "it names the repo and $2"
  }
  files() { git -C "$t/remote/$1" diff-tree --no-commit-id --name-only -r chore/devkit-v9.9.9 2>/dev/null | paste -sd' ' -; }

  echo "-- umbrella_bump_regenerates_wiring_block --"
  fixture umbrella 1
  ok "$(run a/umbrella)" 0 "the bump succeeds"
  ok "$(files a/umbrella)" "docs/2-Building-marola/REPOS.md flake.nix" "the commit carries the pin and the block"
  ok "$(git -C "$t/remote/a/umbrella" show chore/devkit-v9.9.9:docs/2-Building-marola/REPOS.md | sed -n 's/^sub at //p')" \
    "$(git -C "$t/sub" rev-parse HEAD)" "the block was generated with the submodule checked out"
  ok "$(cat "$t/wiring.log" 2>/dev/null)" "docs/2-Building-marola/REPOS.md" "wiring ran once, on REPOS.md"

  echo "-- repo_without_markers_unchanged --"
  rm -f "$t/wiring.log"
  fixture plain 0
  ok "$(run a/plain)" 0 "the bump succeeds"
  ok "$(files a/plain)" "flake.nix" "the commit carries the pin alone"
  ok "$(cat "$t/wiring.log" 2>/dev/null || echo never)" never "wiring never called"

  echo "-- wiring_failure_fails_that_repo --"
  fixture broken 1
  ok "$(run a/broken WIRING_FAIL=1)" 1 "the bump fails"
  ok "$(files a/broken)" "" "nothing pushed"
  ok "$(grep -c wiring "$t/err")" 1 "the failure names wiring"

  echo "-- prepare_needs_no_token --"
  fixture quiet 0
  rm -f "$t/gh.log"
  ok "$(prep a/quiet)" 0 "prepare succeeds"
  ok "$(cat "$t/gh.log" 2>/dev/null || echo never)" never "prepare never calls gh"

  echo "-- publish_runs_no_nix_or_wiring --"
  rm -f "$t/wiring.log" "$t/nix.log"
  ok "$(pub a/quiet)" 0 "publish succeeds"
  ok "$(cat "$t/wiring.log" "$t/nix.log" 2>/dev/null || echo never)" never "publish runs neither"
  ok "$(grep -c 'pr create' "$t/gh.log")" 1 "publish opens the PR"
  ok "$(git -C "$t/remote/a/quiet" log -1 --format=%an chore/devkit-v9.9.9)" bot "the commit is the publisher's"

  echo "-- already_pinned_writes_no_patch --"
  fixture pinned 0 9.9.9
  ok "$(prep a/pinned)" 0 "prepare succeeds"
  ok "$(ls "$t/out" | grep -c '^a_pinned\.')" 0 "no patch for a repo already on the tag"
  ok "$(pub a/pinned)" 0 "publish skips it"

  echo "-- publish_refuses_step_added_to_workflow --"
  FX_WORKFLOW=1 fixture wf 0
  refused a/wf outside-pins inject_step

  echo "-- publish_refuses_second_lock_node --"
  FX_LOCK=1 fixture lock2 0
  refused a/lock2 flake-lock lock_edit nixpkgs

  echo "-- publish_refuses_repos_md_outside_wiring --"
  fixture md 1
  refused a/md repos-md edit_outside

  echo "-- publish_refuses_devkit_node_off_tag --"
  FX_LOCK=1 fixture offtag 0
  refused a/offtag flake-lock lock_edit marola-devkit

  echo "-- publish_refuses_devkit_lock_extra_key --"
  FX_LOCK=1 fixture extrakey 0
  refused a/extrakey flake-lock lock_host

  echo "-- publish_refuses_two_commits --"
  fixture twocommits 0
  refused a/twocommits commits second_commit

  echo "-- publish_refuses_missing_tag --"
  fixture notag 0
  rc="$(prep a/notag)"; ok "$rc" 0 "prepare succeeds"
  : >"$t/err"
  rc="$(pub a/notag BUMP_DEVKIT_URL="file://$t/sub")"
  ok "$([ "$rc" != 0 ] && echo refused)" refused "publish fails"
  ok "$(files a/notag)" "" "nothing pushed"
  ok "$(grep -c '^bump-consumers: a/notag: refused, tag: v9.9.9 not found$' "$t/err")" 1 "it names the repo and the tag"

  echo "-- publish_refuses_base_that_is_not_the_default_branch --"
  fixture otherbase 0
  rc="$(prep a/otherbase)"; ok "$rc" 0 "prepare succeeds"
  git -C "$t/remote/a/otherbase" branch side main
  echo side >"$t/out/a_otherbase.base"
  : >"$t/err"
  rc="$(pub a/otherbase)"
  ok "$([ "$rc" != 0 ] && echo refused)" refused "publish fails"
  ok "$(files a/otherbase)" "" "nothing pushed"
  ok "$(grep -c '^bump-consumers: a/otherbase: refused, base: ' "$t/err")" 1 "it names the repo and base"

  echo "-- publish_accepts_devkit_lock_node --"
  FX_WORKFLOW=1 FX_LOCK=1 fixture good 0
  ok "$(run a/good)" 0 "the bump succeeds"
  ok "$(files a/good)" ".github/workflows/ci.yml flake.lock flake.nix" "the commit carries workflow, lock and flake.nix"
  ok "$(git -C "$t/remote/a/good" show chore/devkit-v9.9.9:flake.lock \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"]["marola-devkit"]["locked"]["rev"])')" \
    "$(git -C "$t/devkit" rev-parse 'v9.9.9^{commit}')" "the lock's devkit node is at the tag's commit"

  [ "$fails" -eq 0 ] || { echo "bump-consumers: self-test FAILED ($fails)" >&2; exit 1; }
  echo "bump-consumers: self-test ok" >&2
  exit 0
fi

usage() { echo "bump-consumers: usage: bump-consumers.sh prepare|publish X.Y.Z DIR | names | repos | tag-rev X.Y.Z" >&2; exit 1; }

if [ "${1:-}" = names ]; then repos | sed 's#^marola-dev/##' | paste -sd, -; exit; fi
if [ "${1:-}" = repos ]; then repos; exit; fi
if [ "${1:-}" = tag-rev ]; then tag_rev "v${2#v}"; exit; fi

one=""
if [ "${1:-}" = --one ]; then mode="${2:-}" version="${3:-}" one="${4:-}" dir="${5:-}"
else mode="${1:-}" version="${2:-}" dir="${3:-}"; fi
version="${version#v}"
case "$mode" in prepare|publish) ;; *) usage ;; esac
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && [ -n "$dir" ] || usage
mkdir -p "$dir"; dir="$(cd "$dir" && pwd)"
tag="v$version" branch="chore/devkit-v$version"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

body() {
  cat <<MD
Before: this repo pins marola-devkit at an older version.

After: it pins [$tag](https://github.com/marola-dev/marola-devkit/releases/tag/$tag): the flake input${1:+ and its \`flake.lock\` node}, every devkit workflow \`@v…\`, \`devkit-ref\` and docs-lint clone, and the plugin marketplace \`ref\`.

Opened by marola-devkit's \`bump-consumers.yml\`; this repo's own CI gates it. What changed in the devkit is in its [CHANGELOG](https://github.com/marola-dev/marola-devkit/blob/$tag/CHANGELOG.md).
MD
}

prepare_one() {
  local repo="$1" slug="${1//\//_}" d="$work/${1//\//_}" base lock="" regen=""
  git clone -q --depth 1 "${BUMP_CONSUMERS_URL:-https://github.com}/$repo" "$d"
  cd "$d"
  base="$(git branch --show-current)"
  git switch -q -C "$branch"
  python3 "$root/scripts/release.py" --consumer . "$version" >/dev/null
  if [ -f flake.lock ] && grep -q '"marola-devkit"' flake.lock; then
    nix flake update marola-devkit
    lock=1
  fi
  if git diff --quiet; then
    echo "bump-consumers: $repo already pins $tag" >&2
    return 0
  fi
  # The umbrella's wiring --check gate reads every repo's devkit pins into this block, so a bump
  # without it never goes green (#78). The new tag's wiring, not this checkout's: the block must
  # match what the bumped repo's own gate will run.
  local repos_md=docs/2-Building-marola/REPOS.md
  if [ -f "$repos_md" ] && grep -q '<!-- wiring:start -->' "$repos_md"; then
    local -a wiring
    read -ra wiring <<<"${BUMP_WIRING:-nix run github:marola-dev/marola-devkit/$tag#wiring --}"
    git submodule update -q --init --depth 1
    "${wiring[@]}" "$repos_md" || { echo "bump-consumers: $repo: wiring failed; not committing a stale $repos_md" >&2; return 1; }
    regen=1
  fi
  git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -qam "chore: marola-devkit $tag" \
    -m "Tested: release.py --consumer moved the pins${lock:+, nix flake update marola-devkit the lock}${regen:+, wiring at $tag regenerated $repos_md}; this repo's CI gates the rest
Co-Authored-By: Claude <noreply@anthropic.com>"
  git format-patch -1 --binary --stdout >"$dir/$slug.patch"
  printf '%s\n' "$base" >"$dir/$slug.base"
  [ -z "$lock" ] || : >"$dir/$slug.lock"
  echo "bump-consumers: $repo prepared" >&2
}

publish_one() {
  local repo="$1" slug="${1//\//_}" d="$work/${1//\//_}" base lock="" pr before rev msg head_ref
  if [ ! -f "$dir/$slug.patch" ]; then
    echo "bump-consumers: $repo has nothing to publish" >&2
    return 0
  fi
  base="$(cat "$dir/$slug.base")"
  [ ! -f "$dir/$slug.lock" ] || lock=1
  # .base comes from the keyless job's artifact: it may only name the default branch.
  head_ref="$(git ls-remote --symref "${BUMP_CONSUMERS_URL:-https://github.com}/$repo" HEAD | awk '$1 == "ref:" { sub("refs/heads/", "", $2); print $2; exit }')"
  [ "$base" = "$head_ref" ] || { echo "bump-consumers: $repo: refused, base: $base is not the default branch" >&2; return 1; }
  git clone -q --depth 1 --branch "$base" "${BUMP_CONSUMERS_URL:-https://github.com}/$repo" "$d"
  cd "$d"
  git switch -q -C "$branch"
  before="$(git rev-parse HEAD)"
  git am -q --keep-cr "$dir/$slug.patch"
  # The patch is the keyless job's output, so it is data here: nothing is pushed that is not a devkit bump.
  rev="$(tag_rev "$tag")" || { echo "bump-consumers: $repo: refused, tag: $tag not found" >&2; return 1; }
  msg="$(python3 "$root/scripts/bump_check.py" . "$before" HEAD "$version" "$rev" 2>&1 >/dev/null)" || {
    echo "bump-consumers: $repo: refused, $msg" >&2
    return 1
  }
  # The patch was committed in the keyless job; the PR's commit should name the bot that pushes it.
  git commit -q --amend --no-edit --reset-author
  git push -q -f origin "$branch"
  pr="$(gh pr list -R "$repo" --head "$branch" --state open --json number -q '.[0].number // empty')"
  if [ -n "$pr" ]; then
    gh pr edit "$pr" -R "$repo" --body "$(body "$lock")" >/dev/null
    echo "bump-consumers: $repo updated #$pr" >&2
  else
    gh pr create -R "$repo" --head "$branch" --base "$base" --title "chore: marola-devkit $tag" --body "$(body "$lock")"
  fi
}

if [ -n "$one" ]; then
  "${mode}_one" "$one"
  exit
fi

if [ "$mode" = prepare ]; then
  printf '%s\n' "$version" >"$dir/version"
else
  [ -n "${GH_TOKEN:-}" ] || { echo "bump-consumers publish: GH_TOKEN is empty; it must be marola-merge-bot's token on every consumer (MIP-0086)" >&2; exit 1; }
  gh auth setup-git
fi
# Each repo in its own process: set -e does not apply inside a function called from `||`.
failed=()
while read -r repo; do
  "$0" --one "$mode" "$version" "$repo" "$dir" || { echo "::error::bump-consumers $mode: $repo failed" >&2; failed+=("$repo"); }
done < <(repos)
[ "${#failed[@]}" -eq 0 ] || { echo "bump-consumers $mode: failed: ${failed[*]}" >&2; exit 1; }
