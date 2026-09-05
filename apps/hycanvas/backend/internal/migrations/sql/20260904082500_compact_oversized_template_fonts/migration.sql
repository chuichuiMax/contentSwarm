-- Old imported templates may contain a complete CJK font collection as inline
-- data URLs. Preserve each font's family/id/source metadata, but remove inline
-- payloads when the whole template exceeds the same 32 MiB safety limit used by
-- current design and template writes. This is a one-time repair; PostgreSQL
-- retains the old row version until normal vacuum, so the migration is atomic.
UPDATE "templates" AS t
SET file = jsonb_set(
  t.file,
  '{fonts}',
  COALESCE((
    SELECT jsonb_agg(
      CASE
        WHEN font->>'url' LIKE 'data:%' THEN font - 'url'
        ELSE font
      END
    )
    FROM jsonb_array_elements(t.file->'fonts') AS font
  ), '[]'::jsonb)
)
WHERE jsonb_typeof(t.file->'fonts') = 'array'
  AND octet_length(t.file::text) > 33554432
  AND EXISTS (
    SELECT 1
    FROM jsonb_array_elements(t.file->'fonts') AS font
    WHERE font->>'url' LIKE 'data:%'
  );
