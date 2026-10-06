-- Allow the featured-cover zone alongside xiaohongshu.
-- Older ContentSwarm cover drafts were tagged contentswarm-cover, which is not
-- a catalog zone. Clear any zone outside the supported set before replacing
-- the CHECK, otherwise existing rows reject the new constraint (SQLSTATE 23514).
UPDATE "designs"
SET "template_zone" = NULL
WHERE "template_zone" IS NOT NULL
  AND "template_zone" NOT IN ('xiaohongshu', 'featured');

ALTER TABLE "designs" DROP CONSTRAINT "designs_template_zone_check";

ALTER TABLE "designs"
    ADD CONSTRAINT "designs_template_zone_check"
    CHECK ("template_zone" IS NULL OR "template_zone" IN ('xiaohongshu', 'featured'));
