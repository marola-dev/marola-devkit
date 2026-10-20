#!/usr/bin/env python3
"""route.py against a fixture graph and a fixture bundle repo (MIP-0084 §7)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROUTE = Path(__file__).resolve().parent.parent / "skills" / "routing" / "scripts" / "route.py"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
GRAPH = {
    "nodes": [
        {"id": "sw", "label": "Swimability", "source_file": "app/scoring/Swimability.scala"},
        {"id": "score", "label": ".score()", "source_file": "app/scoring/Swimability.scala"},
        {"id": "veto", "label": "SafetyVeto", "source_file": "app/scoring/Veto.scala"},
        {
            "id": "om",
            "label": "OpenMeteoClient",
            "source_file": "app/conditions/OpenMeteoClient.scala",
        },
        {"id": "lone", "label": "Lonely", "source_file": "x/Lonely.scala"},
    ]
    + [{"id": f"f{i}", "label": f"filler{i}", "source_file": f"app/f{i}.scala"} for i in range(40)],
    "links": [
        {"source": "sw", "target": "score", "relation": "method"},
        {"source": "score", "target": "veto", "relation": "calls"},
        {"source": "veto", "target": "om", "relation": "calls"},
    ]
    + [{"source": "sw", "target": f"f{i}", "relation": "calls"} for i in range(40)],
}
WIRING = {
    "artifacts": {
        "`ghcr.io/marola-dev/marola-app` image": {
            "pub": ["marola-app `docker.yml`"],
            "pin": ["marola-site `marola-image`"],
            "read": ["marola-site `scripts/board-schema.sh`"],
            "repos": ["marola-app"],
        }
    },
    "dispatch": {
        "submodule-updated": {
            "sent": ["marola-app `notify-umbrella.yml`"],
            "listen": ["marola `pointer-sync.yml`"],
        }
    },
    "bumps": {},
    "deploys": [],
    "devkit": {},
    "calls": {},
    "contracts": {
        "marola-app/cli/src/main/resources/board.schema.json": {
            "copies": ["marola-site/site/board.schema.json"],
            "note": "identical",
        }
    },
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    ).stdout.strip()


class Route(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = self.tmp / "cache"
        self.repo = self.tmp / "marola"
        self.repo.mkdir()
        git("init", "-q", cwd=self.repo)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.repo)

    def tearDown(self) -> None:
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def bundle(
        self, ref: str = "refs/marola/routing-bundle", graph: dict | None = None, **files: str
    ) -> None:
        b = self.tmp / "b"
        if not b.exists():
            b.mkdir()
            git("init", "-q", cwd=b)
        (b / "graph.json").write_text(json.dumps(graph or GRAPH))
        (b / "wiring.json").write_text(json.dumps(WIRING))
        (b / "build.json").write_text(json.dumps({"head": "abcdef1234567", "submodules": ""}))
        for name, text in files.items():
            (b / name).write_text(text)
        git("add", "-A", cwd=b)
        git("commit", "-q", "-m", "bundle", cwd=b)
        git("fetch", "-q", str(b), f"+HEAD:{ref}", cwd=self.repo)

    def route(self, *args: str) -> tuple[int, str]:
        r = subprocess.run(
            [sys.executable, str(ROUTE), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                **GIT_ENV,
                "XDG_CACHE_HOME": str(self.cache),
                "MAROLA_ROUTING_REMOTE": str(self.tmp / "nowhere"),
            },
            timeout=60,
        )
        return r.returncode, r.stdout + r.stderr

    def test_query_matches_label_and_respects_budget(self) -> None:
        self.bundle()
        rc, out = self.route("query", "Swimability")
        nodes = [x for x in out.splitlines() if x.startswith("NODE ")]
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("answers from main@abcdef1 (bundle)"))
        self.assertIn("src=app/scoring/Swimability.scala", nodes[0])
        _, small = self.route("query", "Swimability", "--budget", "60")
        self.assertLess(len(small), len(out))
        self.assertIn("raise --budget", small)
        self.assertIn("NODE Swimability", small)

    def first_node(self, graph: dict, question: str) -> tuple[str, str]:
        self.bundle(graph=graph)
        _, out = self.route("query", question)
        start = next((x for x in out.splitlines() if x.startswith("start: ")), "")
        return next((x for x in out.splitlines() if x.startswith("NODE ")), ""), start

    def test_query_splits_identifiers(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "om",
                    "label": "OpenMeteoClient",
                    "source_file": "app/OpenMeteoClient.scala",
                },
                {"id": "doc", "label": "Open meteo client notes", "source_file": "app/notes.scala"},
            ],
            "links": [{"source": "doc", "target": "om", "relation": "references"}],
        }
        first, _ = self.first_node(graph, "open meteo client")
        self.assertIn("NODE OpenMeteoClient ", first)

    def test_stop_words_dropped(self) -> None:
        graph = {
            "nodes": [
                {"id": "sw", "label": "Swimability", "source_file": "app/Swimability.scala"},
                {"id": "pt", "label": "Pointer", "source_file": "app/Pointer.scala"},
            ],
            "links": [],
        }
        first, start = self.first_node(graph, "the entry point of Swimability")
        self.assertIn("NODE Swimability ", first)
        self.assertNotIn("Pointer", start)

    def test_entry_point_boosted(self) -> None:
        graph = {
            "nodes": [
                {"id": "a_old", "label": "Main", "source_file": "app/old/Main.scala"},
                {
                    "id": "z_cli",
                    "label": "Main",
                    "source_file": "app/cli/Main.scala",
                    "entry": True,
                },
            ]
            + [
                {"id": f"h{i}", "label": f"helper{i}", "source_file": "app/old/H.scala"}
                for i in range(3)
            ],
            "links": [
                {"source": "a_old", "target": f"h{i}", "relation": "calls"} for i in range(3)
            ],
        }
        first, _ = self.first_node(graph, "Main entry point")
        self.assertIn("src=app/cli/Main.scala", first)

    def test_explain_lists_neighbours(self) -> None:
        self.bundle()
        _, out = self.route("explain", "SafetyVeto")
        self.assertIn("EDGE .score() --calls--> SafetyVeto", out)
        self.assertIn("EDGE SafetyVeto --calls--> OpenMeteoClient", out)

    def test_path_found_and_absent(self) -> None:
        self.bundle()
        _, out = self.route("path", "Swimability", "OpenMeteoClient")
        self.assertEqual(
            [x.split(" [")[0] for x in out.splitlines() if x.startswith("NODE")],
            ["NODE Swimability", "NODE .score()", "NODE SafetyVeto", "NODE OpenMeteoClient"],
        )
        _, out = self.route("path", "Swimability", "Lonely")
        self.assertIn("no path between Swimability and Lonely", out)

    def test_wiring_rows_match_term(self) -> None:
        self.bundle()
        _, out = self.route("wiring", "marola-image")
        self.assertIn("read by marola-site `scripts/board-schema.sh`", out)
        self.assertNotIn("dispatch", out)
        _, out = self.route("wiring", "submodule-updated")
        self.assertIn("triggers marola `pointer-sync.yml`", out)

    def test_wiring_lists_contracts(self) -> None:
        self.bundle()
        _, out = self.route("wiring", "board.schema")
        self.assertIn(
            "contract marola-app/cli/src/main/resources/board.schema.json  copied to "
            "marola-site/site/board.schema.json · identical",
            out,
        )

    def test_reads_origin_branch_when_private_ref_absent(self) -> None:
        self.bundle(ref="refs/remotes/origin/routing-bundle")
        rc, out = self.route("query", "OpenMeteoClient")
        self.assertEqual(rc, 0)
        self.assertIn("(bundle)", out)
        self.assertIn("NODE OpenMeteoClient", out)

    def test_local_graph_wins_when_head_matches(self) -> None:
        self.bundle()
        local = self.cache / "marola-graph" / "marola"
        local.mkdir(parents=True)
        g = {"nodes": [{"id": "x", "label": "OnlyLocal", "source_file": "l.py"}], "links": []}
        (local / "graph.json").write_text(json.dumps(g))
        head = git("rev-parse", "HEAD", cwd=self.repo)
        (local / "build.json").write_text(json.dumps({"head": head, "submodules": ""}))
        _, out = self.route("query", "OnlyLocal")
        self.assertIn("local graph", out.splitlines()[0])
        self.assertIn("NODE OnlyLocal", out)
        git("commit", "-q", "--allow-empty", "-m", "next", cwd=self.repo)
        _, out = self.route("query", "OnlyLocal")
        self.assertIn("(bundle)", out.splitlines()[0])

    def test_no_bundle_no_network_exits_zero(self) -> None:
        for args in (["query", "Swimability"], ["wiring", "x"], ["bench"]):
            rc, out = self.route(*args)
            self.assertEqual(rc, 0, args)
            self.assertEqual(
                out.strip(), "no routing bundle; use `git grep` (or `route fetch` once online)"
            )
        rc, out = self.route("fetch")
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", out)

    def test_oversized_file_refused(self) -> None:
        big = json.dumps({"nodes": [], "links": [], "pad": "x" * (20 * 1024 * 1024)})
        self.bundle(graph=json.loads(big))
        rc, out = self.route("query", "x")
        self.assertEqual(rc, 1)
        self.assertIn("graph.json is over 20 MB; refused", out)
        self.assertNotIn("Traceback", out)

    def test_bench_counts_right_first(self) -> None:
        self.bundle()
        qs = [
            {
                "id": 1,
                "via": "query",
                "ask": "Swimability",
                "expect": ["scoring/Swimability.scala"],
            },
            {"id": 2, "via": "query", "ask": "OpenMeteoClient", "expect": ["nowhere.scala"]},
            {"id": 3, "via": "wiring", "ask": "marola-image", "expect": ["board-schema.sh"]},
        ]
        f = self.tmp / "bench.yaml"
        f.write_text(json.dumps(qs))
        _, out = self.route("bench", str(f))
        self.assertIn("right first: 2/3", out)
        self.assertIn("MISS   2", out)

    def test_impact_reverse_depth(self) -> None:
        self.bundle()
        _, out = self.route("impact", "OpenMeteoClient")
        rows = [x for x in out.splitlines() if x.startswith("  ")]
        self.assertEqual(rows[0].split()[:2], ["1", "app/scoring/Veto.scala"])
        self.assertIn("SafetyVeto", rows[0])
        self.assertEqual(rows[1].split()[:2], ["2", "app/scoring/Swimability.scala"])
        self.assertIn(".score()", rows[1])
        # `method` is not a call: Swimability's 40 callees never appear.
        self.assertNotIn("f0.scala", out)
        _, out = self.route("impact", "OpenMeteoClient", "--depth", "1")
        self.assertNotIn("Swimability.scala", out)
        _, out = self.route("impact", "app/scoring/Veto.scala")
        self.assertIn("1  app/scoring/Swimability.scala", out)
        self.assertNotIn("app/scoring/Veto.scala  ", out.split("\n", 2)[2])

    def test_impact_adds_contract_readers(self) -> None:
        wiring = {
            **WIRING,
            "artifacts": {
                **WIRING["artifacts"],
                "`marola-corpus-<tag>.tar.gz` release asset": {
                    "pub": ["marola-corpus `release.yml`"],
                    "pin": ["marola-ml `corpus.version`"],
                    "read": ["marola-ml `scripts/corpus-fetch.sh`"],
                    "repos": ["marola-corpus"],
                },
            },
            "bumps": {"marola-ml `corpus.version`": ["marola-ml `ci.yml`"]},
        }
        self.bundle(**{"wiring.json": json.dumps(wiring)})
        _, out = self.route("impact", "board.schema.json")
        lines = out.splitlines()
        self.assertEqual(lines[1], "contract  marola-app/cli/src/main/resources/board.schema.json")
        self.assertEqual(lines[2], "  copied to     marola-site/site/board.schema.json")
        self.assertIn("  read by       marola-site `scripts/board-schema.sh`", out)
        self.assertIn("  pinned in     marola-site `marola-image`", out)
        _, out = self.route("impact", "corpus.version")
        rows = [x for x in out.splitlines() if x.startswith("  ")]
        self.assertEqual(rows[0], "  read by       marola-ml `scripts/corpus-fetch.sh`")
        self.assertIn("  bumped by     marola-ml `ci.yml`", out)
        f = self.tmp / "bench.yaml"
        f.write_text(
            json.dumps(
                [
                    {
                        "id": 1,
                        "via": "impact",
                        "ask": "board.schema.json",
                        "expect": ["board-schema.sh"],
                    },
                    {"id": 2, "via": "impact", "ask": "OpenMeteoClient", "expect": ["Veto.scala"]},
                    {
                        "id": 3,
                        "via": "impact",
                        "ask": "OpenMeteoClient",
                        "expect": ["OpenMeteoClient.scala"],
                    },
                ]
            )
        )
        _, out = self.route("bench", str(f))
        self.assertIn("right first: 2/3", out)
        self.assertIn("MISS   3", out)

    def test_impact_prints_lower_bound(self) -> None:
        self.bundle()
        for x in ("OpenMeteoClient", "board.schema.json", "Lonely", "nothing.json"):
            rc, out = self.route("impact", x)
            lines = out.strip().splitlines()
            self.assertEqual(rc, 0, x)
            self.assertEqual(lines[0], "answers from main@abcdef1 (bundle)", x)
            self.assertEqual(lines[-1], "(lower bound: name-resolved calls only)", x)
        self.assertIn("no graph node or wiring row names nothing.json", out)

    def decisions(self) -> None:
        def row(mip: str, text: str, kind: str = "alternative") -> dict:
            sec = "1" if kind == "summary" else "9"
            return {"mip": mip, "section": sec, "status": "Draft", "kind": kind, "text": text}

        rows = [
            row("MIP-0001", "pass pass pass pass the pass", "summary"),
            row("MIP-0076", "graphify LLM pass (rejected): tokens"),
            row(
                "MIP-0085",
                "graphify LLM pass (rejected): about 0.6M input tokens per run over the "
                "whole tree of every repo and every doc in it",
            ),
        ] + [row(f"MIP-00{i:02d}", f"pass number{i}") for i in range(10, 16)]
        self.bundle(**{"decisions.json": json.dumps({"rows": rows})})

    def test_why_ranks_bm25(self) -> None:
        self.bundle()
        _, out = self.route("why", "graphify LLM pass")
        self.assertIn("no decisions.json", out)
        self.decisions()
        rc, out = self.route("why", "graphify LLM pass")
        lines = out.splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "answers from main@abcdef1 (bundle)")
        # The shorter row first (length normalised), then the longer; a common word's repeats
        # (MIP-0001's five "pass") never outrank two rare words.
        self.assertEqual(lines[1], "MIP-0076 §9  graphify LLM pass (rejected): tokens [Draft]")
        self.assertTrue(lines[2].startswith("MIP-0085 §9  graphify LLM pass"), lines[2])
        self.assertNotIn("MIP-0001", "\n".join(lines[1:3]))
        _, out = self.route("why", "nothing like it")
        self.assertIn("no decision row matches 'nothing like it'", out)

    def test_bench_answers_why(self) -> None:
        self.decisions()
        qs = [
            {"id": 1, "via": "why", "ask": "graphify LLM pass", "expect": ["MIP-0085"]},
            {"id": 2, "via": "why", "ask": "graphify LLM pass", "expect": ["MIP-0099"]},
        ]
        f = self.tmp / "bench.yaml"
        f.write_text(json.dumps(qs))
        _, out = self.route("bench", str(f))
        self.assertIn("right first: 1/2", out)
        self.assertIn("MISS   2  why", out)

    def test_docs_and_code_directions(self) -> None:
        sw = "marola-app/core/src/main/scala/marola/scoring/Swimability.scala"
        mip = "docs/MIPs/MIP-0031-swimability.md"
        link = {"doc": mip, "section": "§5", "token": "Swimability.score", "line": 9}
        dc = {
            "docs": {
                mip: {
                    "mip": "MIP-0031",
                    "links": [{**link, "file": sw, "symbol": ".score()"}],
                    "ambiguous": [],
                },
                "marola-site/docs/4-reference.md": {
                    "mip": None,
                    "links": [],
                    "ambiguous": [
                        {
                            "section": "Reference",
                            "token": "Swimability",
                            "line": 3,
                            "candidates": [sw, "marola-site/x/Swimability.js"],
                        }
                    ],
                },
            },
            "code": {sw: [{**link, "symbol": ".score()"}]},
        }
        self.bundle(**{"doc_code.json": json.dumps(dc)})
        _, out = self.route("docs", "Swimability")
        lines = out.splitlines()
        self.assertEqual(lines[0], "answers from main@abcdef1 (bundle)")
        self.assertTrue(lines[1].startswith(f"{mip} §5  `Swimability.score`"), lines)
        self.assertIn("ambiguous", lines[2])
        self.assertIn("marola-site/x/Swimability.js", lines[2])
        _, out = self.route("docs", "scoring/Swimability.scala")
        self.assertIn(mip, out)
        _, out = self.route("code", "MIP-0031")
        self.assertTrue(out.splitlines()[1].startswith(sw), out)
        self.assertIn("`Swimability.score`", out)
        _, out = self.route("code", "MIP-0099")
        self.assertIn("no doc matches 'MIP-0099'", out)
        qs = [
            {"id": 1, "via": "docs", "ask": "Swimability", "expect": ["MIP-0031"]},
            {"id": 2, "via": "code", "ask": "MIP-0031", "expect": ["scoring/Swimability.scala"]},
        ]
        f = self.tmp / "bench.yaml"
        f.write_text(json.dumps(qs))
        _, out = self.route("bench", str(f))
        self.assertIn("right first: 2/2", out)

    def meaning(self) -> None:
        texts = {
            "veto": "SafetyVeto A dangerous flag: the swim is forbidden, whatever the score. "
            "app/scoring/Veto.scala",
            "om": "OpenMeteoClient Fetches the sea and weather forecast for a beach. "
            "app/conditions/OpenMeteoClient.scala",
            "sw": "Swimability The 0-100 score. app/scoring/Swimability.scala",
            "lone": "Lonely swim swim swim swim swim swim x/Lonely.scala",
            "gone": "GoneNode The weather forecast, from an older graph. gone.py",
        } | {f"f{i}": f"filler{i} app/f{i}.scala" for i in range(40)}
        self.bundle(**{"meaning.json": json.dumps({"schema": 1, "nodes": texts})})

    def test_meaning_absent_says_so(self) -> None:
        self.bundle()
        rc, out = self.route("query", "--meaning", "where is a dangerous swim forbidden")
        lines = out.strip().splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "answers from main@abcdef1 (bundle)")
        self.assertEqual(len(lines), 2, out)
        self.assertIn("no meaning.json", lines[1])
        self.assertIn("`route query` without --meaning", lines[1])
        self.assertIn("git grep", lines[1])

    def test_meaning_ranks_bm25(self) -> None:
        self.meaning()
        rc, out = self.route("query", "--meaning", "where is a dangerous swim forbidden")
        nodes = [x for x in out.splitlines() if x.startswith("NODE ")]
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], "answers from main@abcdef1 (bundle)")
        # Two rare words beat six repeats of a common one: BM25 saturates a term's count.
        self.assertTrue(nodes[0].startswith("NODE SafetyVeto [src=app/scoring/Veto.scala "), out)
        self.assertTrue(nodes[1].startswith("NODE Lonely [src=x/Lonely.scala "), out)
        _, out = self.route("query", "weather forecast at sea", "--meaning")
        nodes = [x for x in out.splitlines() if x.startswith("NODE ")]
        self.assertIn("src=app/conditions/OpenMeteoClient.scala", nodes[0])
        # A node the graph no longer has prints by its id, never a traceback.
        self.assertEqual(nodes[1], "NODE gone")
        _, out = self.route("query", "--meaning", "nothing like it")
        self.assertIn("no node's text matches 'nothing like it'", out)
        _, out = self.route("query", "weather forecast at sea")
        self.assertNotIn("SafetyVeto", out)

    def test_bench_answers_meaning(self) -> None:
        qs = [
            {"id": 1, "via": "meaning", "ask": "dangerous swim forbidden", "expect": ["Veto.scala"]}
        ]
        f = self.tmp / "bench.yaml"
        f.write_text(json.dumps(qs))
        # No meaning.json: the question goes to `route query`, as before MIP-0085 T8.
        self.bundle()
        _, out = self.route("bench", str(f))
        self.assertIn("right first: 0/1", out)
        self.meaning()
        _, out = self.route("bench", str(f))
        self.assertIn("right first: 1/1", out)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.argv.remove("--self-test")
    unittest.main(verbosity=2)
