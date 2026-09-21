-- Allow the featured-cover zone alongside xiaohongshu. Same column, wider
-- CHECK; existing rows are untouched.
ALTER TABLE "designs" DROP CONSTRAINT "designs_template_zone_check";

ALTER TABLE "designs"
    ADD CONSTRAINT "designs_template_zone_check"
    CHECK ("template_zone" IS NULL OR "template_zone" IN ('xiaohongshu', 'featured'));
