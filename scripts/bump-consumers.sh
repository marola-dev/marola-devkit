#!/usr/bin/env bash
# bump-consumers: after a devkit release, one PR per repo in .github/consumers.txt on
# chore/devkit-vX.Y.Z, moving its pins (release.py --consumer), where it commits one the
# flake.lock's devkit node, and where it has one REPOS.md's wiring block (BUMP_WIRING overrides
# the new tag's `nix run …#wiring`). A re-run force-updates the branch and edits the open PR. GH_TOKEN must
# reach every repo; the PRs are authored by its owner. One repo failing doesn't stop the others.
#   scripts/bump-consumers.sh X.Y.Z
#   scripts/bump-consumers.sh --self-test
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

repos() { grep -Ev '^[[:space:]]*(#|$)' "${1:-$root/.github/consumers.txt}"; }

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
  printf '#!/usr/bin/env bash\necho "gh $*" >>"%s/gh.log"\n' "$t" >"$t/bin/gh"
  cat >"$t/bin/wiring" <<EOF
#!/usr/bin/env bash
set -euo pipefail
echo "\$*" >>"$t/wiring.log"
[ -z "\${WIRING_FAIL:-}" ] || exit 3
[ -e sub/.git ] || { echo "sub is not checked out" >&2; exit 4; }
sed "s/^sub at .*/sub at \$(git -C sub rev-parse HEAD)/" "\$1" >"\$1.new"
mv "\$1.new" "\$1"
EOF
  chmod +x "$t/bin/gh" "$t/bin/wiring"
  git init -q -b main "$t/sub"
  git -C "$t/sub" commit -q --allow-empty -m sub
  fixture() {   # fixture NAME MARKERS(0|1)
    local d="$t/src/$1"
    git init -q -b main "$d"
    mkdir -p "$d/docs/2-Building-marola"
    echo 'inputs.marola-devkit.url = "github:marola-dev/marola-devkit/v0.0.1";' >"$d/flake.nix"
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
  run() {   # run REPO [ENV=VALUE...]: one bump against the fixtures, its exit status echoed
    local repo="$1" rc=0; shift
    env PATH="$t/bin:$PATH" BUMP_CONSUMERS_URL="file://$t/remote" BUMP_WIRING=wiring "$@" \
      bash "$root/scripts/bump-consumers.sh" --one 9.9.9 "$repo" >/dev/null 2>"$t/err" || rc=$?
    echo "$rc"
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

  [ "$fails" -eq 0 ] || { echo "bump-consumers: self-test FAILED ($fails)" >&2; exit 1; }
  echo "bump-consumers: self-test ok" >&2
  exit 0
fi

one=""
if [ "${1:-}" = --one ]; then one="$3"; shift; fi
version="${1:-}"; version="${version#v}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bump-consumers: usage: bump-consumers.sh X.Y.Z" >&2; exit 1; }
tag="v$version" branch="chore/devkit-v$version"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

body() {
  cat <<MD
Before: this repo pins marola-devkit at an older version.

After: it pins [$tag](https://github.com/marola-dev/marola-devkit/releases/tag/$tag): the flake input${1:+ and its \`flake.lock\` node}, every devkit workflow \`@v…\`, \`devkit-ref\` and docs-lint clone, and the plugin marketplace \`ref\`.

Opened by marola-devkit's \`bump-consumers.yml\`; this repo's own CI gates it. What changed in the devkit is in its [CHANGELOG](https://github.com/marola-dev/marola-devkit/blob/$tag/CHANGELOG.md).
MD
}

bump() {
  local repo="$1" dir="$work/${1//\//_}" base lock="" regen=""
  git clone -q --depth 1 "${BUMP_CONSUMERS_URL:-https://github.com}/$repo" "$dir"
  cd "$dir"
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
Cost: n/a (automation)
Co-Authored-By: Claude <noreply@anthropic.com>"
  git push -q -f origin "$branch"
  local pr
  pr="$(gh pr list -R "$repo" --head "$branch" --state open --json number -q '.[0].number // empty')"
  if [ -n "$pr" ]; then
    gh pr edit "$pr" -R "$repo" --body "$(body "$lock")" >/dev/null
    echo "bump-consumers: $repo updated #$pr" >&2
  else
    gh pr create -R "$repo" --head "$branch" --base "$base" --title "chore: marola-devkit $tag" --body "$(body "$lock")"
  fi
}

if [ -n "$one" ]; then
  bump "$one"
  exit
fi

[ -n "${GH_TOKEN:-}" ] || { echo "bump-consumers: GH_TOKEN is empty; give marola-devkit access to the org secret MAROLA_BUMP_PAT (docs/3-development.md, Releases)" >&2; exit 1; }
# Each repo in its own process: set -e does not apply inside a function called from `||`.
gh auth setup-git
failed=()
while read -r repo; do
  "$0" --one "$version" "$repo" || { echo "::error::bump-consumers: $repo failed" >&2; failed+=("$repo"); }
done < <(repos)
[ "${#failed[@]}" -eq 0 ] || { echo "bump-consumers: failed: ${failed[*]}" >&2; exit 1; }
