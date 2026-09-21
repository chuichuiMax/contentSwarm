-- A template draft has one current reusable template. Link templates back to
-- their source design so repeated saves update the same row instead of creating
-- duplicate cards.
ALTER TABLE "templates"
    ADD COLUMN "source_design_id" UUID;

ALTER TABLE "templates"
    ADD CONSTRAINT "templates_source_design_id_fkey"
    FOREIGN KEY ("source_design_id") REFERENCES "designs"("id")
    ON DELETE SET NULL ON UPDATE CASCADE;

-- Remove only repeated cards with the same owner, source design, title and
-- scope. Keep the latest snapshot; differently named templates remain intact.
WITH duplicates AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY "owner_id", file->>'id', title, "workspace_id", "collection_id", visibility
               ORDER BY "updated_at" DESC, "created_at" DESC, id DESC
           ) AS position
    FROM "templates"
    WHERE file->>'id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
      AND EXISTS (SELECT 1 FROM "designs" d WHERE d.id = (file->>'id')::uuid)
)
DELETE FROM "templates" t
USING duplicates d
WHERE t.id = d.id AND d.position > 1;

-- Historical saves did not persist the relationship. Link the latest template
-- for each owner/design pair; older differently named snapshots stay available.
WITH latest AS (
    SELECT id,
           (file->>'id')::uuid AS source_design_id,
           row_number() OVER (
               PARTITION BY "owner_id", file->>'id'
               ORDER BY "updated_at" DESC, "created_at" DESC, id DESC
           ) AS position
    FROM "templates"
    WHERE file->>'id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
      AND EXISTS (SELECT 1 FROM "designs" d WHERE d.id = (file->>'id')::uuid)
)
UPDATE "templates" t
SET "source_design_id" = latest.source_design_id
FROM latest
WHERE t.id = latest.id AND latest.position = 1;

CREATE UNIQUE INDEX "templates_owner_id_source_design_id_key"
    ON "templates"("owner_id", "source_design_id")
    WHERE "source_design_id" IS NOT NULL;
