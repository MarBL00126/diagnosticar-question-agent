"""SQLite layout of the knowledge base.

``ancestor`` stores the full ``is_a`` closure (proper ancestors only, with the
shortest distance). For HPO 2026-02-16 it is about 200k rows, a few MB, and
lets both the loader and ad-hoc SQL answer subsumption without recursion.
"""

from __future__ import annotations

KB_SCHEMA_VERSION = 1
PAGE_SIZE = 4096

TABLES = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE term (
    hpo_id TEXT PRIMARY KEY,
    label_en TEXT NOT NULL,
    label_es TEXT,
    obsolete INTEGER NOT NULL,
    replaced_by TEXT,
    depth INTEGER,
    requires_testing INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE alt_id (
    alt_id TEXT PRIMARY KEY,
    hpo_id TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE parent (
    hpo_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    PRIMARY KEY (hpo_id, parent_id)
) WITHOUT ROWID;

CREATE TABLE ancestor (
    hpo_id TEXT NOT NULL,
    ancestor_id TEXT NOT NULL,
    distance INTEGER NOT NULL,
    PRIMARY KEY (hpo_id, ancestor_id)
) WITHOUT ROWID;

CREATE TABLE disease (
    disease_id TEXT PRIMARY KEY,
    name TEXT
) WITHOUT ROWID;

CREATE TABLE annotation (
    disease_id TEXT NOT NULL,
    hpo_id TEXT NOT NULL,
    negated INTEGER NOT NULL,
    frequency REAL,
    onset TEXT,
    PRIMARY KEY (disease_id, hpo_id, negated)
) WITHOUT ROWID;

CREATE TABLE gene (
    gene_symbol TEXT PRIMARY KEY,
    ncbi_gene_id TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE gene_disease (
    gene_symbol TEXT NOT NULL,
    disease_id TEXT NOT NULL,
    association_type TEXT,
    PRIMARY KEY (gene_symbol, disease_id)
) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX ancestor_by_ancestor ON ancestor (ancestor_id, hpo_id);
CREATE INDEX annotation_by_term ON annotation (hpo_id, disease_id);
CREATE INDEX gene_disease_by_disease ON gene_disease (disease_id, gene_symbol);
"""
