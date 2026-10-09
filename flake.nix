{
  description = "marola-devkit — the shared dev-flow harness (stack, uprd, issues, cost-split, …), a base dev shell and just module (MIP-0070)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    # Same h0ffmann/nix-config labs as marola, one nixpkgs closure via `follows`.
    lint = {
      url = "github:h0ffmann/nix-config?dir=labs/lint";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    agentic = {
      url = "github:h0ffmann/nix-config/labs/agentic?dir=labs/agentic";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, lint, agentic }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        lib = pkgs.lib;

        # scripts/pr_label_nlp.py imports sklearn and scripts/wiring.py yaml, so both live in this python3.
        python = pkgs.python3.withPackages (ps: [ ps.scikit-learn ps.pyyaml ]);
        runtimeDeps = [
          pkgs.bash pkgs.coreutils pkgs.findutils pkgs.gnugrep pkgs.gnused pkgs.gawk pkgs.perl
          pkgs.git pkgs.gh pkgs.jq pkgs.curl python pkgs.graphify
        ];

        # PATH name -> script. `pr-flow`, not `pr`: that would shadow coreutils.
        tools = {
          stack = "scripts/stack.sh";
          uprd = "scripts/uprd.sh";
          uprds = "scripts/uprds.sh";
          pr-flow = "scripts/pr.sh";
          issues = "scripts/issues.sh";
          cost-split = "scripts/cost-split.py";
          cost-fill = "scripts/cost-fill.sh";
          agents-check = "scripts/agents-check.sh";
          mip-resolve = "scripts/mip-resolve.sh";
          branches = "scripts/branches.sh";
          pr-label = "scripts/pr-label.sh";
          pr-label-nlp = "scripts/pr_label_nlp.py";
          backfill-pr-labels = "scripts/backfill-pr-labels.sh";
          mip-stack = "scripts/mip-stack.sh";
          docs-mip-stack = "scripts/docs-mip-stack.sh";
          deps-stack = "scripts/deps-stack.sh";
          deps-merge = "scripts/deps-merge.sh";
          ruleset-sync = "scripts/ruleset-sync.sh";
          api-docs-push = "scripts/api-docs-push.sh";
          gha-runner = "scripts/gha-runner.sh";
          setup-runners = "scripts/setup-runners.sh";
          runner-preflight = "scripts/runner-preflight.sh";
          temps = "scripts/temps.sh";
          graph = "scripts/graph.sh";
          workflow-runners = "scripts/workflow_runners.py";
          docs-lint = "scripts/docs_lint.py";
          skills-vendor = "scripts/skills_vendor.py";
          wiring = "scripts/wiring.py";
        };

        # The whole tree is installed with its layout intact under share/marola-devkit: the scripts
        # find lib/, fixtures/, .github/labels.yml and agents/invariants.md relative to themselves.
        devkit = pkgs.stdenvNoCC.mkDerivation {
          pname = "marola-devkit";
          version = "0.8.0";
          src = lib.cleanSource self;
          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pkgs.bash python ];
          dontBuild = true;
          installPhase = ''
            runHook preInstall
            share=$out/share/marola-devkit
            mkdir -p $share $out/bin
            cp -r scripts agents plugins .githooks .github devkit.just $share/
            mkdir -p $share/.claude
            cp .claude/statusline.sh $share/.claude/statusline.sh
            ${lib.concatStrings (lib.mapAttrsToList (name: path: ''
              makeWrapper $share/${path} $out/bin/${name} \
                --prefix PATH : $out/bin:${lib.makeBinPath runtimeDeps} \
                --set-default MAROLA_INVARIANTS_BLOCK $share/agents/invariants.md
            '') tools)}
            runHook postInstall
          '';
          meta.description = "marola's dev-flow tools on PATH";
        };

        # A consumer's shellHook: `.devkit` points at the pinned tree, so its justfile can
        # `import '.devkit/devkit.just'` and, if it opts in, `core.hooksPath` can be `.devkit/.githooks`.
        shellHook = ''
          devkit_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
          ln -sfn ${devkit}/share/marola-devkit "$devkit_root/.devkit"
        '';

        consumerTools = [ devkit pkgs.just pkgs.gh pkgs.jq pkgs.git python ] ++ lint.lib.${system}.tools;
      in
      {
        packages = { default = devkit; marola-devkit = devkit; }
          // lib.mapAttrs (name: _: pkgs.runCommand name { meta.description = "marola-devkit's ${name}"; } ''
            mkdir -p $out/bin && ln -s ${devkit}/bin/${name} $out/bin/${name}
          '') tools;

        apps = lib.mapAttrs (name: _: {
          type = "app";
          program = "${devkit}/bin/${name}";
          meta.description = "marola-devkit's ${name}";
        }) tools;

        # What a consuming repo's flake appends, the same shape as labs/lint's `lib.<system>.tools`.
        lib = {
          tools = consumerTools;
          inherit shellHook;
          justModule = "${devkit}/share/marola-devkit/devkit.just";
          invariants = "${devkit}/share/marola-devkit/agents/invariants.md";
        };

        devShells.default = pkgs.mkShell {
          name = "marola-devkit";
          packages = consumerTools ++ agentic.lib.${system}.tools;
          inherit (agentic.lib.${system}.env) BWRAP_BIN;
          shellHook = ''
            echo "marola-devkit dev shell"
            git config core.hooksPath .githooks 2>/dev/null || true
            echo "Run 'just' to see available commands."
          '';
        };

        checks = {
          devkit = devkit;
          self-tests = pkgs.runCommand "marola-devkit-self-tests" {
            nativeBuildInputs = runtimeDeps ++ lint.lib.${system}.tools;
          } ''
            cp -r ${lib.cleanSource self} src && chmod -R u+w src && cd src
            export HOME=$TMPDIR GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com \
              GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
            git init -q && git add -A && git commit -qm check
            patchShebangs scripts plugins .githooks
            bash tests/self-tests.sh
            touch $out
          '';
        };
      });
}
