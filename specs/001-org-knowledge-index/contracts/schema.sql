-- org-index schema v1. Requires SQLite >= 3.41 with FTS5 and sqlite-vec >= 0.1.6 (vec0 metadata
-- columns). Validated against sqlite-vec 0.1.9, the version the pinned nixpkgs ships.
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;

-- schema_version, embed_model, embed_digest, embed_dims, chunker_version, built_at, fingerprint
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE source (
  repo           TEXT PRIMARY KEY,          -- 'marola-dev/marola-app'
  default_branch TEXT NOT NULL,
  commit_sha     TEXT NOT NULL,
  issues_through TEXT                       -- max updatedAt fetched, ISO 8601
);

-- One row per unique normalized content: the dedup key across repos.
CREATE TABLE document (
  id          INTEGER PRIMARY KEY,
  content_sha TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL CHECK (kind IN ('doc', 'code', 'config', 'issue', 'pr', 'comment')),
  lang        TEXT,                         -- 'en' | 'pt' | NULL for code
  title       TEXT NOT NULL
);

CREATE TABLE location (
  id           INTEGER PRIMARY KEY,
  document_id  INTEGER NOT NULL REFERENCES document (id) ON DELETE CASCADE,
  repo         TEXT NOT NULL REFERENCES source (repo),
  path         TEXT NOT NULL,               -- file path, or 'issues/398', 'pull/625#r123'
  url          TEXT NOT NULL,
  is_canonical INTEGER NOT NULL DEFAULT 0 CHECK (is_canonical IN (0, 1)),
  UNIQUE (repo, path)
);
CREATE UNIQUE INDEX location_one_canonical ON location (document_id) WHERE is_canonical = 1;

-- One row per unique normalized chunk text. The header comes from the canonical location.
CREATE TABLE chunk (
  id          INTEGER PRIMARY KEY,
  content_sha TEXT NOT NULL UNIQUE,
  embed_key   TEXT NOT NULL,                -- sha256(model || header || text): vector reuse key
  header      TEXT NOT NULL,                -- 'marola-dev/marola · docs/MIPs/MIP-0055-….md › 5.4'
  text        TEXT NOT NULL,
  tokens      INTEGER NOT NULL
);
CREATE INDEX chunk_embed_key ON chunk (embed_key);

CREATE TABLE chunk_occurrence (
  chunk_id    INTEGER NOT NULL REFERENCES chunk (id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL REFERENCES document (id) ON DELETE CASCADE,
  ord         INTEGER NOT NULL,
  PRIMARY KEY (document_id, ord)
);
CREATE INDEX chunk_occurrence_chunk ON chunk_occurrence (chunk_id);

-- 'MIP-0055', 'marola-dev/marola#398', 'marola-dev/marola-app@16ab0c4'
CREATE TABLE edge (
  document_id INTEGER NOT NULL REFERENCES document (id) ON DELETE CASCADE,
  ref         TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('mip', 'issue', 'pr', 'commit', 'file')),
  PRIMARY KEY (document_id, ref)
);
CREATE INDEX edge_ref ON edge (ref);

-- Brute-force KNN: exact, and ~30 ms at the org's ~10k chunks. kind/repo/lang are metadata
-- columns so they filter inside the KNN instead of after it.
CREATE VIRTUAL TABLE chunk_vec USING vec0 (
  chunk_id  INTEGER PRIMARY KEY,
  embedding float[1024] distance_metric=cosine,
  kind      TEXT,
  repo      TEXT,
  lang      TEXT
);

-- External content: text lives once, in chunk. remove_diacritics 2 so 'agua' finds 'água'.
CREATE VIRTUAL TABLE chunk_fts USING fts5 (
  header, text,
  content = 'chunk', content_rowid = 'id',
  tokenize = 'unicode61 remove_diacritics 2'
);
