-- Verify the GTM engine schema in Supabase. Read-only: run it in the SQL Editor
-- (Supabase dashboard -> SQL Editor -> New query) and read the status column.
--
-- You normally do not need this. Database.__init__ issues the whole schema as
-- CREATE TABLE IF NOT EXISTS on every connect, so the first API request or CLI run
-- creates anything missing. This is for answering "is it actually there?" without
-- guessing, and for spotting a half-applied schema after a failed migration.

WITH expected(name) AS (
    VALUES ('campaigns'), ('runs'), ('companies'), ('pages'), ('leads'),
           ('suppressions'), ('drafts'), ('outreach_events'), ('run_progress')
)
SELECT
    e.name AS table_name,
    CASE WHEN t.tablename IS NULL THEN 'MISSING' ELSE 'ok' END AS status,
    COALESCE((SELECT count(*) FROM information_schema.columns c
              WHERE c.table_schema = 'public' AND c.table_name = e.name), 0) AS columns
FROM expected e
LEFT JOIN pg_tables t ON t.schemaname = 'public' AND t.tablename = e.name
ORDER BY status DESC, e.name;
