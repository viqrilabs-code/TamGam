-- infra/init-db.sql
-- Runs automatically on first PostgreSQL container startup
-- Enables the pgvector extension required for AI Tutor RAG search

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- For gen_random_uuid() fallback