set shell := ["bash", "-euo", "pipefail", "-c"]

import 'devkit.just'

default:
    @just --list

# Point git at this repo's hooks (the dev shell does it too).
install-hooks:
    git config core.hooksPath .githooks

# The devkit's own gates: lint, every script's --self-test, the invariants block, docs-lint, the plugin.
quality:
    #!/usr/bin/env bash
    set -euo pipefail
    for tool in ruff shellcheck actionlint; do command -v "$tool" >/dev/null || { echo "quality: $tool not installed — run inside 'nix develop'" >&2; exit 1; }; done
    ruff check .
    ruff format --check .
    # Errors only: the warnings/infos came over from marola, which never gated shellcheck.
    shellcheck --severity=error scripts/*.sh scripts/lib/*.sh plugins/marola-devkit/hooks/*.sh .githooks/* .claude/statusline.sh tests/*.sh
    actionlint
    bash tests/self-tests.sh
    scripts/agents-check.sh
    python3 scripts/docs_lint.py
    python3 scripts/skills_vendor.py check --lock plugins/marola-devkit/skills/skills.lock
    if command -v claude >/dev/null; then claude plugin validate .; else echo "claude not on PATH — skipping plugin validate"; fi

# The git hooks' contract (README): fast checks at commit, the full gate at push.
precommit:
    ruff check .
    shellcheck --severity=error scripts/*.sh scripts/lib/*.sh plugins/marola-devkit/hooks/*.sh .githooks/* .claude/statusline.sh tests/*.sh
    scripts/agents-check.sh

prepush: quality
