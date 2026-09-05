-- Keep a design's template-zone membership in its summary row so dashboard
-- listings can filter drafts without loading every snapshot blob.
ALTER TABLE "designs" ADD COLUMN "template_zone" TEXT;

ALTER TABLE "designs"
    ADD CONSTRAINT "designs_template_zone_check"
    CHECK ("template_zone" IS NULL OR "template_zone" IN ('xiaohongshu'));

-- Drafts created before template-zone metadata was persisted used this exact
-- title. Backfill them once; future membership is written from file.meta.
UPDATE "designs"
SET "template_zone" = 'xiaohongshu'
WHERE title = '小红书模板专区';
