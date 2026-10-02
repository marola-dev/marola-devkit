-- The queries `org-index query` runs, usable by hand in any SQLite with sqlite-vec loaded:
--   sqlite3 org-index.sqlite -cmd '.load vec0'      (path from: python3 -c 'import sqlite_vec; print(sqlite_vec.loadable_path())')
-- Parameters: :q = the query vector as float32 bytes or a JSON array, :t = an FTS5 query string,
-- :k = results wanted, :kind / :repo = filters or NULL.

-- vec: KNN with filters applied inside the scan. Trap: vec0 takes only `column = value` here:
-- `kind = coalesce(:kind, kind)` returns zero rows without an error (sqlite-vec 0.1.9), so the
-- tool appends `AND kind = :kind` / `AND repo = :repo` only when the filter is set.
SELECT v.chunk_id, v.distance, c.header
FROM chunk_vec v JOIN chunk c ON c.id = v.chunk_id
WHERE v.embedding MATCH :q AND k = :k
  AND v.kind = :kind AND v.repo = :repo
ORDER BY v.distance;

-- fts: BM25, header weighted 2x over body.
SELECT c.id AS chunk_id, bm25(chunk_fts, 2.0, 1.0) AS score, c.header
FROM chunk_fts JOIN chunk c ON c.id = chunk_fts.rowid
WHERE chunk_fts MATCH :t
ORDER BY score
LIMIT :k;

-- hybrid: reciprocal rank fusion of the two lists (k = 60, the usual constant).
WITH v AS (
  SELECT chunk_id AS id, row_number() OVER (ORDER BY distance) AS rk
  FROM chunk_vec WHERE embedding MATCH :q AND k = 50
), f AS (
  SELECT rowid AS id, row_number() OVER (ORDER BY bm25(chunk_fts, 2.0, 1.0)) AS rk
  FROM chunk_fts WHERE chunk_fts MATCH :t LIMIT 50
)
SELECT c.id AS chunk_id,
       coalesce(1.0 / (60 + v.rk), 0) + coalesce(1.0 / (60 + f.rk), 0) AS score,
       c.header
FROM chunk c LEFT JOIN v ON v.id = c.id LEFT JOIN f ON f.id = c.id
WHERE v.id IS NOT NULL OR f.id IS NOT NULL
ORDER BY score DESC
LIMIT :k;

-- Every place a hit lives (the dedup made visible), canonical first.
SELECT l.repo, l.path, l.url, l.is_canonical
FROM chunk_occurrence o JOIN location l ON l.document_id = o.document_id
WHERE o.chunk_id = :chunk_id
ORDER BY l.is_canonical DESC, l.repo;

-- Graph expansion: documents that reference what a hit's document references (one hop).
SELECT DISTINCT e2.document_id, e2.ref
FROM chunk_occurrence o
JOIN edge e1 ON e1.document_id = o.document_id
JOIN edge e2 ON e2.ref = e1.ref AND e2.document_id <> o.document_id
WHERE o.chunk_id = :chunk_id;
