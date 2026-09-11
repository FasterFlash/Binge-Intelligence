-- Runs automatically on first container boot only.
-- If you change this after first run, you must recreate the volume
-- (docker compose down -v) for it to re-apply.

CREATE EXTENSION IF NOT EXISTS vector;