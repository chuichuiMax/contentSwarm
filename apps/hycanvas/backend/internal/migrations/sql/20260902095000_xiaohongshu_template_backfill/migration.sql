-- Templates saved from historical Xiaohongshu drafts predate snapshot-level
-- zone metadata. Their embedded file id still points to the source design, so
-- restore the catalog tag without creating duplicate templates.
UPDATE "templates" AS t
SET category = COALESCE(NULLIF(t.category, ''), '小红书'),
    tags = CASE
        WHEN '小红书' = ANY(COALESCE(t.tags, ARRAY[]::TEXT[])) THEN t.tags
        ELSE array_append(COALESCE(t.tags, ARRAY[]::TEXT[]), '小红书')
    END,
    "updated_at" = now()
FROM "designs" AS d
WHERE t.file->>'id' = d.id::TEXT
  AND d."template_zone" = 'xiaohongshu';
