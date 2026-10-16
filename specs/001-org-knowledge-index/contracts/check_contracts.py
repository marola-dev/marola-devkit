#!/usr/bin/env python3
"""Run schema.sql and every statement in queries.sql against sqlite-vec on synthetic data.

python3 specs/001-org-knowledge-index/contracts/check_contracts.py   # needs `sqlite-vec` importable
"""

import random
import sqlite3
import sys
from pathlib import Path

import sqlite_vec

HERE = Path(__file__).parent
DIMS = 1024


def unit_vector(rnd: random.Random) -> bytes:
    v = [rnd.gauss(0, 1) for _ in range(DIMS)]
    norm = sum(x * x for x in v) ** 0.5
    return sqlite_vec.serialize_float32([x / norm for x in v])


def statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def vec_query(stmt: str, kind: str | None, repo: str | None) -> str:
    # The filter the tool omits when unset; see the trap note in queries.sql.
    if kind is None:
        stmt = stmt.replace("AND v.kind = :kind ", "")
    if repo is None:
        stmt = stmt.replace("AND v.repo = :repo", "")
    return stmt


def main() -> int:
    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.executescript((HERE / "schema.sql").read_text())

    rnd = random.Random(0)
    db.executemany(
        "INSERT INTO source VALUES (?, 'main', 'abc', NULL)",
        [("marola-dev/marola",), ("marola-dev/marola-devkit",)],
    )
    db.execute("INSERT INTO document VALUES (1, 'd1', 'doc', 'pt', 'Correntes')")
    db.execute("INSERT INTO document VALUES (2, 'd2', 'issue', 'en', '#398')")
    db.execute("INSERT INTO location VALUES (1, 1, 'marola-dev/marola-devkit', 'a.md', 'u1', 1)")
    db.execute("INSERT INTO location VALUES (2, 1, 'marola-dev/marola', 'a.md', 'u2', 0)")
    try:
        db.execute("INSERT INTO location VALUES (3, 1, 'marola-dev/marola', 'b.md', 'u3', 1)")
        raise AssertionError("a document accepted two canonical locations")
    except sqlite3.IntegrityError:
        pass
    db.executemany("INSERT INTO edge VALUES (?, 'MIP-0055', 'mip')", [(1,), (2,)])
    for i in range(1, 201):
        text = {7: "Corrente de retorno na Joaquina, água fria", 9: "trait KnowledgeStore"}.get(
            i, f"chunk {i}"
        )
        kind = "doc" if i % 2 else "code"
        repo = "marola-dev/marola" if i % 3 else "marola-dev/marola-devkit"
        db.execute(
            "INSERT INTO chunk VALUES (?, ?, ?, ?, ?, 10)",
            (i, f"c{i}", f"e{i}", f"{repo} · a.md › h{i}", text),
        )
        db.execute("INSERT INTO chunk_occurrence VALUES (?, 1, ?)", (i, i))
        db.execute(
            "INSERT INTO chunk_vec VALUES (?, ?, ?, ?, 'pt')", (i, unit_vector(rnd), kind, repo)
        )
    db.execute("INSERT INTO chunk_fts (chunk_fts) VALUES ('rebuild')")

    vec, fts, hybrid, locations, graph = statements((HERE / "queries.sql").read_text())
    q = unit_vector(rnd)
    for kind, repo in [(None, None), ("doc", None), ("doc", "marola-dev/marola")]:
        rows = db.execute(vec_query(vec, kind, repo), {"q": q, "k": 5, "kind": kind, "repo": repo})
        ids = [r[0] for r in rows]
        assert len(ids) == 5, (kind, repo, ids)
        for cid in ids:
            got = db.execute(
                "SELECT kind, repo FROM chunk_vec WHERE chunk_id = ?", (cid,)
            ).fetchone()
            assert kind in (None, got[0]) and repo in (None, got[1]), (kind, repo, got)
    assert [r[0] for r in db.execute(fts, {"t": "agua", "k": 5})] == [7], "diacritics"
    assert [r[0] for r in db.execute(fts, {"t": "knowledgestore", "k": 5})] == [9], "case"
    assert db.execute(hybrid, {"q": q, "t": "agua", "k": 3}).fetchone()[0] == 7, "rrf"
    assert db.execute(locations, {"chunk_id": 7}).fetchone()[3] == 1, "canonical first"
    assert db.execute(graph, {"chunk_id": 7}).fetchall() == [(2, "MIP-0055")], "graph"

    vv = db.execute("SELECT sqlite_version(), vec_version()").fetchone()
    print(f"contracts ok (sqlite {vv[0]}, sqlite-vec {vv[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
