-- SPEC §4.7: a dedicated SELECT-only role for the generated-SQL executor.
-- Defence in depth alongside sqlglot AST validation.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'janus_ro') THEN
        CREATE ROLE janus_ro LOGIN PASSWORD 'janus_ro';
    END IF;
END
$$;

-- Strip everything, then grant back only SELECT.
REVOKE ALL ON SCHEMA public FROM janus_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM janus_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM janus_ro;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM janus_ro;
REVOKE CREATE ON SCHEMA public FROM janus_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT CONNECT ON DATABASE janus TO janus_ro;
GRANT USAGE ON SCHEMA public TO janus_ro;
GRANT SELECT ON companies, filings, xbrl_facts, filing_sections, facts TO janus_ro;
-- deliberately NOT granted: section_chunks (embeddings are not for the SQL path)

ALTER ROLE janus_ro SET statement_timeout = '10s';
ALTER ROLE janus_ro SET default_transaction_read_only = on;
ALTER ROLE janus_ro SET search_path = public;
