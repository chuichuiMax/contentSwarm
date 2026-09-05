from __future__ import annotations

from typing import Any

from yuxi.content.model.industry.pack import (
    IndustryPackCatalog,
    IndustryPackEvaluator,
    IndustryPackLoader,
    IndustryPackRegressionEvaluator,
    IndustryPackRegressionMetrics,
    IndustryPackValidator,
    industry_pack_hash,
)


class ValidateIndustryPackHandler:
    def execute(
        self,
        *,
        record: Any,
        variable_mappings: list[Any],
        combination_groups: list[dict[str, Any]],
        rule_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        pack = IndustryPackLoader.load(
            record,
            variable_mappings=variable_mappings,
            combination_groups=combination_groups,
        )
        catalog = IndustryPackCatalog(
            method_codes=frozenset(item["code"] for item in rule_bundle.get("methods") or []),
            title_formula_codes=frozenset(item["code"] for item in rule_bundle.get("title_formulas") or []),
            body_formula_codes=frozenset(item["code"] for item in rule_bundle.get("content_formulas") or []),
            variable_codes=frozenset(item["code"] for item in rule_bundle.get("variables") or []),
        )
        validation = IndustryPackValidator().validate(pack, catalog)
        evaluation = IndustryPackEvaluator().evaluate(pack, validation)
        return {
            "pack": pack.model_dump(mode="json"),
            "pack_hash": industry_pack_hash(pack),
            "validation": {
                "valid": validation.valid,
                "errors": list(validation.errors),
                "warnings": list(validation.warnings),
            },
            "evaluation": {
                "passed": evaluation.passed,
                "metrics": evaluation.metrics,
            },
        }


class EvaluateIndustryPackRegressionHandler:
    def execute(
        self,
        *,
        pack: dict[str, Any],
        metrics: IndustryPackRegressionMetrics,
        source_run_ids: list[str],
        sample_count: int,
        candidate_recommendations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        loaded_pack = IndustryPackLoader.load_from_mapping(pack)
        evaluation = IndustryPackRegressionEvaluator().evaluate(loaded_pack, metrics)
        return {
            "pack_version_id": loaded_pack.id,
            "pack_hash": industry_pack_hash(loaded_pack),
            "source_run_ids": source_run_ids,
            "sample_count": sample_count,
            "metrics": evaluation.metrics,
            "passed": evaluation.passed,
            "failed_gates": list(evaluation.failed_gates),
            "candidate_recommendations": candidate_recommendations,
            "candidate_only": True,
        }


__all__ = ["EvaluateIndustryPackRegressionHandler", "ValidateIndustryPackHandler"]
