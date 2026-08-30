-- Applied AFTER embeddings are loaded: building HNSW on a populated table is far
-- faster than incrementally maintaining it during a bulk insert.
CREATE INDEX IF NOT EXISTS section_chunks_hnsw_idx
    ON section_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
ANALYZE section_chunks;
ANALYZE xbrl_facts;
