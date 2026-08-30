-- Janus schema. See SPEC.md §4.6.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
    cik             TEXT PRIMARY KEY,
    ticker          TEXT,
    name            TEXT NOT NULL,
    sector          TEXT,
    sic             TEXT,
    fiscal_year_end TEXT
);

CREATE TABLE IF NOT EXISTS filings (
    id              BIGSERIAL PRIMARY KEY,
    cik             TEXT NOT NULL REFERENCES companies(cik),
    accession       TEXT UNIQUE NOT NULL,
    form_type       TEXT NOT NULL,              -- '10-K' | '10-Q'
    filed_date      DATE NOT NULL,
    period_end      DATE NOT NULL,
    fiscal_label    TEXT,                       -- e.g. 'FY2024Q3'
    source_url      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS filings_cik_idx ON filings (cik, period_end);

-- Canonicalised financial facts. `concept_raw` preserves what the filer actually used.
CREATE TABLE IF NOT EXISTS xbrl_facts (
    id              BIGSERIAL PRIMARY KEY,
    cik             TEXT NOT NULL REFERENCES companies(cik),
    filing_id       BIGINT REFERENCES filings(id),
    accession       TEXT,                       -- accession the fact was reported in
    concept_raw     TEXT NOT NULL,
    concept_canon   TEXT NOT NULL,              -- from the concept-mapping layer
    value_num       NUMERIC NOT NULL,
    unit            TEXT NOT NULL,
    period_start    DATE,
    period_end      DATE NOT NULL,
    period_type     TEXT NOT NULL,              -- 'duration' | 'instant'
    fiscal_label    TEXT,
    fiscal_year     INT,
    fiscal_period   TEXT,                       -- 'Q1'|'Q2'|'Q3'|'Q4'|'FY'
    form            TEXT,
    filed_date      DATE,
    is_restated     BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS xbrl_facts_lookup_idx ON xbrl_facts (cik, concept_canon, period_end);
CREATE INDEX IF NOT EXISTS xbrl_facts_fiscal_idx ON xbrl_facts (cik, concept_canon, fiscal_label);
CREATE INDEX IF NOT EXISTS xbrl_facts_restated_idx ON xbrl_facts (is_restated);

CREATE TABLE IF NOT EXISTS filing_sections (
    id              BIGSERIAL PRIMARY KEY,
    filing_id       BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    item_code       TEXT NOT NULL,              -- '1A' | '7' | '3' | ...
    item_title      TEXT,
    content         TEXT NOT NULL,
    char_count      INT
);
CREATE INDEX IF NOT EXISTS filing_sections_filing_idx ON filing_sections (filing_id, item_code);

CREATE TABLE IF NOT EXISTS section_chunks (
    id              BIGSERIAL PRIMARY KEY,
    section_id      BIGINT NOT NULL REFERENCES filing_sections(id) ON DELETE CASCADE,
    cik             TEXT NOT NULL,              -- denormalised for filtering
    fiscal_label    TEXT,                       -- denormalised for the sequential path
    item_code       TEXT NOT NULL,
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,
    embedding       VECTOR(384) NOT NULL,
    embed_model     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS section_chunks_filter_idx ON section_chunks (cik, fiscal_label, item_code);

-- Point-in-time view: only as-originally-filed facts. This is what the SQL path sees.
CREATE OR REPLACE VIEW facts AS
SELECT f.id, f.cik, c.ticker, c.name AS company_name, f.concept_canon, f.concept_raw,
       f.value_num, f.unit, f.period_start, f.period_end, f.period_type,
       f.fiscal_label, f.fiscal_year, f.fiscal_period, f.form, f.filed_date
FROM xbrl_facts f
JOIN companies c ON c.cik = f.cik
WHERE f.is_restated = FALSE;
