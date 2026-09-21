-- Featured cover templates uploaded before the producer fix stored a random
-- UUID as the design file's asset id; the real upload id only appeared inside
-- the asset URL (.../api/v1/assets/{id}/content). Server-side rendering
-- resolves image bytes through the uploads service by that id, so every one of
-- these templates rendered a blank canvas. Rewrite both the file asset id and
-- the image node's source.assetId to the upload id parsed from the URL.
-- Idempotent: rows already pointing at the real id are untouched.
UPDATE "templates"
SET "file" = jsonb_set(
        jsonb_set("file", '{assets,0,id}', to_jsonb(m.real_id)),
        '{pages,0,children,0,source,assetId}', to_jsonb(m.real_id)
    ),
    "updated_at" = CURRENT_TIMESTAMP
FROM (
    SELECT
        "id",
        (regexp_match("file"->'assets'->0->>'url', '/api/v1/assets/([0-9a-fA-F-]{36})/content'))[1] AS real_id
    FROM "templates"
) m
WHERE "templates"."id" = m."id"
  AND m.real_id IS NOT NULL
  AND "file"->'assets'->0->>'id' IS DISTINCT FROM m.real_id;
