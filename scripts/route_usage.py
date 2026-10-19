#!/usr/bin/env python3
"""route-usage: which Claude Code sessions routed a "where is it" question (MIP-0084 §5.5).

route-usage [DIR ...]   # default ${CLAUDE_CONFIG_DIR:-~/.claude}/projects
route-usage --self-test

Local and read-only: it reads transcripts and prints counts, never their text, and never uploads,
since transcripts hold private conversations.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROUTE = re.compile(r"\broute(?:\.py)?\s+(?:query|explain|path|wiring|fetch|status|bench)\b")
GRAPH = re.compile(r"\bgraph(?:\.sh)?\s+(?:query|explain|path|build)\b")
# The umbrella's page only: other repos have their own REPOS.md.
WIRING = re.compile(r"2-Building-marola/REPOS|wiring:start")
# What a person says that would make the agent's routing call prompted rather than its own choice.
ASKED = re.compile(r"\b(route|routing|graph|wiring)\b", re.I)


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b if isinstance(b, str) else str(b.get("text", ""))
            for b in content
            if isinstance(b, (str, dict))
        )
    return ""


def records(path: Path):
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def scan(dirs: list[Path]) -> dict[str, dict]:
    """Sessions by id; a subagent's transcript carries its parent's id and adds to it."""
    sessions: dict[str, dict] = {}
    for path in sorted(p for d in dirs for p in d.rglob("*.jsonl")):
        for r in records(path):
            sid = r.get("sessionId") or path.stem
            s = sessions.setdefault(
                sid,
                {"repo": "?", "day": "?", "route": 0, "graph": 0, "wiring": 0}
                | {"prompted": None, "hook": False, "asked": False},
            )
            if s["repo"] == "?" and r.get("cwd"):
                s["repo"] = Path(r["cwd"]).name
            if s["day"] == "?" and r.get("timestamp"):
                s["day"] = str(r["timestamp"])[:10]
            msg = r.get("message") or {}
            if r.get("type") == "user" and isinstance(msg.get("content"), str):
                said = msg["content"]
                # Tags (<task-notification>, <command-name>) are the harness talking, not a person.
                if not said.lstrip().startswith("<") and ASKED.search(said):
                    s["asked"] = True
            elif r.get("type") == "attachment":
                a = r.get("attachment") or {}
                if a.get("hookEvent") == "SessionStart" and "▸ routing:" in text_of(
                    a.get("content")
                ):
                    s["hook"] = True
            elif r.get("type") == "assistant" and isinstance(msg.get("content"), list):
                for b in msg["content"]:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    arg = json.dumps(b.get("input") or {})
                    hits = {
                        "route": len(ROUTE.findall(arg)),
                        "graph": len(GRAPH.findall(arg)),
                        "wiring": 1 if WIRING.search(arg) else 0,
                    }
                    for k, n in hits.items():
                        s[k] += n
                    if any(hits.values()) and s["prompted"] is None:
                        s["prompted"] = s["asked"]
    return sessions


def report(sessions: dict[str, dict]) -> list[str]:
    used = sorted(
        (s["day"], s["repo"], sid, s)
        for sid, s in sessions.items()
        if s["route"] or s["graph"] or s["wiring"]
    )
    lines = [
        f"{day}  {repo:<15} {sid[:8]}  route {s['route']}  graph {s['graph']}  wiring {s['wiring']}  "
        + ("prompted" if s["prompted"] else "unprompted")
        + ("  hook" if s["hook"] else "")
        for day, repo, sid, s in used
    ]
    unprompted = sum(1 for *_, s in used if not s["prompted"])
    hooked = sum(1 for s in sessions.values() if s["hook"])
    lines.append(
        f"{len(sessions)} sessions, {len(used)} routed ({unprompted} unprompted), "
        f"{hooked} saw the hook's line"
    )
    return lines


def self_test() -> None:
    def rec(**kw) -> str:
        return json.dumps(kw)

    def bash(cmd: str) -> dict:
        return {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}

    def said(sid: str, text: str) -> str:
        return rec(type="user", sessionId=sid, message={"role": "user", "content": text})

    def did(sid: str, *uses: dict) -> str:
        return rec(
            type="assistant", sessionId=sid, message={"role": "assistant", "content": list(uses)}
        )

    hook = rec(
        type="attachment",
        sessionId="aaaa1111",
        cwd="/home/x/marola",
        timestamp="2026-10-20T09:00:00Z",
        attachment={
            "type": "hook_additional_context",
            "hookEvent": "SessionStart",
            "content": ["▸ routing: bundle main@e77c298 (2 h old)"],
        },
    )
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "-home-x-marola"
        (proj / "aaaa1111" / "subagents").mkdir(parents=True)
        (proj / "aaaa1111.jsonl").write_text(
            "\n".join(
                [
                    hook,
                    said("aaaa1111", "where does the board schema come from?"),
                    did(
                        "aaaa1111",
                        bash('python3 .claude/skills/routing/scripts/route.py query "BoardSchema"'),
                    ),
                    did(
                        "aaaa1111",
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {
                                "file_path": "/home/x/marola/docs/2-Building-marola/REPOS.md"
                            },
                        },
                    ),
                    "{not json",
                ]
            )
        )
        (proj / "aaaa1111" / "subagents" / "agent-1.jsonl").write_text(
            did("aaaa1111", bash("route wiring marola-image"))
        )
        (proj / "bbbb2222.jsonl").write_text(
            "\n".join(
                [
                    rec(
                        type="user",
                        sessionId="bbbb2222",
                        cwd="/home/x/marola-app",
                        timestamp="2026-10-21T10:00:00Z",
                        message={"role": "user", "content": "use route to find the scorer"},
                    ),
                    did("bbbb2222", bash("just graph query 'Swimability score'")),
                ]
            )
        )
        (proj / "cccc3333.jsonl").write_text(
            "\n".join(
                [said("cccc3333", "fix the typo"), did("cccc3333", bash("git grep -n routes"))]
            )
        )
        s = scan([Path(d)])
        assert set(s) == {"aaaa1111", "bbbb2222", "cccc3333"}, f"groups_by_session: {sorted(s)}"
        a, b, c = s["aaaa1111"], s["bbbb2222"], s["cccc3333"]
        assert (a["route"], a["wiring"], a["graph"]) == (2, 1, 0), f"counts_route_and_wiring: {a}"
        assert a["prompted"] is False and a["hook"] is True, f"unprompted_with_hook: {a}"
        assert (b["graph"], b["prompted"], b["hook"]) == (1, True, False), f"prompted_graph: {b}"
        assert (c["route"], c["graph"], c["wiring"]) == (0, 0, 0), (
            f"git_grep_routes_not_counted: {c}"
        )
        assert a["repo"] == "marola" and a["day"] == "2026-10-20", f"repo_and_day: {a}"
        lines = report(s)
        assert len(lines) == 3, f"one_line_per_routing_session_plus_total: {lines}"
        assert lines[0].startswith("2026-10-20  marola") and "unprompted" in lines[0], lines[0]
        assert lines[-1] == "3 sessions, 2 routed (1 unprompted), 1 saw the hook's line", lines[-1]
        assert "board schema" not in "\n".join(lines), "never_prints_transcript_text"
    print("route-usage self-test: ok")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--self-test"]:
        self_test()
        return 0
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    print("\n".join(report(scan([Path(a) for a in argv] or [base]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
