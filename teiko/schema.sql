-- Loblaw Bio immune-profiling database schema.
--
-- The CSV is one wide row per sample. It is normalised here into four
-- entities so that each fact is stored once:
--
--   projects ─< subjects ─< samples ─< cell_counts >─ populations
--
-- * Subject attributes (condition, age, sex, treatment, response) are constant
--   across a subject's samples, so they live on `subjects`, not on every sample.
-- * Cell counts are stored long (one row per sample x population) rather than
--   one column per population. Adding a new population, e.g. from a larger
--   panel, is then a data change, not a schema migration.

PRAGMA foreign_keys = ON;

CREATE TABLE projects (
    project_id  TEXT PRIMARY KEY
);

CREATE TABLE subjects (
    subject_id  TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects (project_id),
    condition   TEXT NOT NULL,                 -- indication, e.g. melanoma
    age         INTEGER NOT NULL CHECK (age >= 0),
    sex         TEXT NOT NULL CHECK (sex IN ('M', 'F')),
    treatment   TEXT NOT NULL,                 -- 'none' for healthy controls
    response    TEXT CHECK (response IN ('yes', 'no'))  -- NULL when untreated
);

CREATE TABLE samples (
    sample_id                  TEXT PRIMARY KEY,
    subject_id                 TEXT NOT NULL REFERENCES subjects (subject_id),
    sample_type                TEXT NOT NULL,  -- PBMC or WB (whole blood)
    time_from_treatment_start  INTEGER NOT NULL CHECK (time_from_treatment_start >= 0)
);

CREATE TABLE populations (
    population  TEXT PRIMARY KEY,              -- e.g. cd4_t_cell
    label       TEXT NOT NULL,                 -- e.g. CD4 T cell
    sort_order  INTEGER NOT NULL UNIQUE
);

CREATE TABLE cell_counts (
    sample_id   TEXT NOT NULL REFERENCES samples (sample_id),
    population  TEXT NOT NULL REFERENCES populations (population),
    count       INTEGER NOT NULL CHECK (count >= 0),
    PRIMARY KEY (sample_id, population)
) WITHOUT ROWID;

-- Indexes on the columns the analyses filter by.
CREATE INDEX idx_subjects_cohort ON subjects (condition, treatment);
CREATE INDEX idx_subjects_project ON subjects (project_id);
CREATE INDEX idx_samples_subject ON samples (subject_id);
CREATE INDEX idx_samples_type_time ON samples (sample_type, time_from_treatment_start);

-- Part 2: relative frequency of each population within each sample.
-- One row per sample x population, with the columns the brief specifies.
-- Totals come from a grouped subquery rather than a window function: SQLite
-- can push a filter on sample_id into the subquery, so looking up a few
-- samples does not recompute totals for the whole table.
CREATE VIEW sample_frequencies AS
SELECT
    cc.sample_id                          AS sample,
    t.total_count                         AS total_count,
    cc.population                         AS population,
    cc.count                              AS count,
    100.0 * cc.count / t.total_count      AS percentage
FROM cell_counts AS cc
JOIN (
    SELECT sample_id, SUM(count) AS total_count
    FROM cell_counts
    GROUP BY sample_id
) AS t ON t.sample_id = cc.sample_id;
