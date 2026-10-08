#!/usr/bin/env bash
# bump-consumers: after a devkit release, one PR per repo in .github/consumers.txt on
# chore/devkit-vX.Y.Z, moving its pins (release.py --consumer) and, where it commits one, the
# flake.lock's devkit node. A re-run force-updates the branch and edits the open PR. GH_TOKEN must
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
  local repo="$1" dir="$work/${1//\//_}" base lock=""
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
  git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -qam "chore: marola-devkit $tag" \
    -m "Tested: release.py --consumer moved the pins${lock:+, nix flake update marola-devkit the lock}; this repo's CI gates the rest
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

[ -n "${GH_TOKEN:-}" ] || { echo "bump-consumers: GH_TOKEN is empty; set the MAROLA_BUMP_TOKEN secret on marola-devkit (docs/3-development.md, Releases)" >&2; exit 1; }
# Each repo in its own process: set -e does not apply inside a function called from `||`.
gh auth setup-git
failed=()
while read -r repo; do
  "$0" --one "$version" "$repo" || { echo "::error::bump-consumers: $repo failed" >&2; failed+=("$repo"); }
done < <(repos)
[ "${#failed[@]}" -eq 0 ] || { echo "bump-consumers: failed: ${failed[*]}" >&2; exit 1; }
