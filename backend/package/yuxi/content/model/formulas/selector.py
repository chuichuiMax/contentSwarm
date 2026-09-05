from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


FormulaKind = Literal["title", "body"]


@dataclass(frozen=True, slots=True)
class FormulaCandidateDefinition:
    code: str
    kind: FormulaKind
    rule_version_id: str
    enabled: bool = True
    required_variable_codes: tuple[str, ...] = ()
    required_evidence_types: tuple[str, ...] = ()
    semantic_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FormulaCandidatePool:
    combination_group_id: str
    rule_version_id: str
    title_formula_codes: tuple[str, ...]
    body_formula_codes: tuple[str, ...]
    allowed_formula_pairs: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self) -> None:
        if not self.title_formula_codes or not self.body_formula_codes:
            raise ValueError("公式候选池不能为空")
        if len(set(self.title_formula_codes)) != len(self.title_formula_codes):
            raise ValueError("标题公式候选不能重复")
        if len(set(self.body_formula_codes)) != len(self.body_formula_codes):
            raise ValueError("正文公式候选不能重复")
        for title_code, body_code in self.allowed_formula_pairs:
            if title_code not in self.title_formula_codes or body_code not in self.body_formula_codes:
                raise ValueError("显式公式配对必须属于当前组合组候选池")

    def validate_pair(self, title_code: str, body_code: str) -> None:
        if title_code not in self.title_formula_codes or body_code not in self.body_formula_codes:
            raise ValueError("标题和正文公式必须属于同一个命中组合组")
        if self.allowed_formula_pairs and (title_code, body_code) not in self.allowed_formula_pairs:
            raise ValueError("当前标题与正文公式不在允许配对中")


@dataclass(frozen=True, slots=True)
class FormulaSelectionRequest:
    available_variable_codes: frozenset[str] = frozenset()
    available_evidence_types: frozenset[str] = frozenset()
    title_signals: frozenset[str] = frozenset()
    body_signals: frozenset[str] = frozenset()
    content_goal_code: str | None = None
    channel_target: str | None = None
    persona_target: str | None = None
    agent_title_ranking: tuple[str, ...] = ()
    agent_body_ranking: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FormulaScore:
    formula_code: str
    score: int
    score_details: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FormulaRejection:
    formula_code: str
    reasons: tuple[str, ...]
    missing_variable_codes: tuple[str, ...] = ()
    missing_evidence_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FormulaSelectionDecision:
    status: Literal["selected", "blocked_by_formula"]
    combination_group_id: str
    eligible_title_formulas: tuple[FormulaScore, ...]
    eligible_body_formulas: tuple[FormulaScore, ...]
    rejected_title_formulas: tuple[FormulaRejection, ...]
    rejected_body_formulas: tuple[FormulaRejection, ...]
    selected_title_formula_code: str | None
    selected_body_formula_code: str | None
    title_selection_reason: str | None
    body_selection_reason: str | None
    selection_mode: Literal["deterministic", "agent_assisted"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "combination_group_id": self.combination_group_id,
            "eligible_title_formulas": [item.to_dict() for item in self.eligible_title_formulas],
            "eligible_body_formulas": [item.to_dict() for item in self.eligible_body_formulas],
            "rejected_title_formulas": [item.to_dict() for item in self.rejected_title_formulas],
            "rejected_body_formulas": [item.to_dict() for item in self.rejected_body_formulas],
            "selected_title_formula_code": self.selected_title_formula_code,
            "selected_body_formula_code": self.selected_body_formula_code,
            "title_selection_reason": self.title_selection_reason,
            "body_selection_reason": self.body_selection_reason,
            "selection_mode": self.selection_mode,
        }


class FormulaSelector:
    """从已命中组内分别选择一个标题公式和一个正文公式。"""

    def select(
        self,
        pool: FormulaCandidatePool,
        definitions: list[FormulaCandidateDefinition],
        request: FormulaSelectionRequest,
    ) -> FormulaSelectionDecision:
        self._validate_agent_ranking(request.agent_title_ranking, pool.title_formula_codes, "标题")
        self._validate_agent_ranking(request.agent_body_ranking, pool.body_formula_codes, "正文")
        definitions_by_key = {(item.kind, item.code): item for item in definitions}
        title_scores, title_rejections = self._rank(
            kind="title",
            codes=pool.title_formula_codes,
            definitions=definitions_by_key,
            pool=pool,
            signals=request.title_signals,
            agent_ranking=request.agent_title_ranking,
            request=request,
        )
        body_scores, body_rejections = self._rank(
            kind="body",
            codes=pool.body_formula_codes,
            definitions=definitions_by_key,
            pool=pool,
            signals=request.body_signals,
            agent_ranking=request.agent_body_ranking,
            request=request,
        )
        selected_pair = self._select_pair(pool, title_scores, body_scores)
        mode = "agent_assisted" if request.agent_title_ranking or request.agent_body_ranking else "deterministic"
        if selected_pair is None:
            return FormulaSelectionDecision(
                status="blocked_by_formula",
                combination_group_id=pool.combination_group_id,
                eligible_title_formulas=tuple(title_scores),
                eligible_body_formulas=tuple(body_scores),
                rejected_title_formulas=tuple(title_rejections),
                rejected_body_formulas=tuple(body_rejections),
                selected_title_formula_code=None,
                selected_body_formula_code=None,
                title_selection_reason=None,
                body_selection_reason=None,
                selection_mode=mode,
            )

        title, body = selected_pair
        pool.validate_pair(title.formula_code, body.formula_code)
        return FormulaSelectionDecision(
            status="selected",
            combination_group_id=pool.combination_group_id,
            eligible_title_formulas=tuple(title_scores),
            eligible_body_formulas=tuple(body_scores),
            rejected_title_formulas=tuple(title_rejections),
            rejected_body_formulas=tuple(body_rejections),
            selected_title_formula_code=title.formula_code,
            selected_body_formula_code=body.formula_code,
            title_selection_reason=self._reason(title, mode),
            body_selection_reason=self._reason(body, mode),
            selection_mode=mode,
        )

    @staticmethod
    def _validate_agent_ranking(ranking: tuple[str, ...], pool_codes: tuple[str, ...], label: str) -> None:
        if len(set(ranking)) != len(ranking):
            raise ValueError(f"Agent 的{label}公式排序不能重复")
        outside = sorted(set(ranking) - set(pool_codes))
        if outside:
            raise ValueError(f"Agent 不能提交候选池外的{label}公式: {', '.join(outside)}")

    def _rank(
        self,
        *,
        kind: FormulaKind,
        codes: tuple[str, ...],
        definitions: dict[tuple[FormulaKind, str], FormulaCandidateDefinition],
        pool: FormulaCandidatePool,
        signals: frozenset[str],
        agent_ranking: tuple[str, ...],
        request: FormulaSelectionRequest,
    ) -> tuple[list[FormulaScore], list[FormulaRejection]]:
        scores: list[FormulaScore] = []
        rejections: list[FormulaRejection] = []
        agent_positions = {code: index for index, code in enumerate(agent_ranking)}
        for code in codes:
            definition = definitions.get((kind, code))
            reasons: list[str] = []
            if definition is None:
                reasons.append("unknown_formula")
                missing_variables: tuple[str, ...] = ()
                missing_evidence: tuple[str, ...] = ()
            else:
                if definition.rule_version_id != pool.rule_version_id:
                    reasons.append("rule_version_mismatch")
                if not definition.enabled:
                    reasons.append("disabled")
                missing_variables = tuple(
                    sorted(set(definition.required_variable_codes) - request.available_variable_codes)
                )
                missing_evidence = tuple(
                    sorted(set(definition.required_evidence_types) - request.available_evidence_types)
                )
                if missing_variables:
                    reasons.append("missing_variables")
                if missing_evidence:
                    reasons.append("missing_evidence")
            if reasons:
                rejections.append(FormulaRejection(code, tuple(reasons), missing_variables, missing_evidence))
                continue

            assert definition is not None
            score_details = {
                "signal_match": len(set(definition.semantic_tags) & signals) * 10,
                "variable_coverage": len(definition.required_variable_codes) * 3,
                "evidence_coverage": len(definition.required_evidence_types) * 5,
                "goal_match": 8 if request.content_goal_code in definition.semantic_tags else 0,
                "channel_match": 6 if request.channel_target in definition.semantic_tags else 0,
                "persona_match": 6 if request.persona_target in definition.semantic_tags else 0,
                "agent_rank": max(0, 1000 - agent_positions[code] * 100) if code in agent_positions else 0,
            }
            scores.append(FormulaScore(code, sum(score_details.values()), score_details))
        scores.sort(key=lambda item: (-item.score, item.formula_code))
        rejections.sort(key=lambda item: item.formula_code)
        return scores, rejections

    @staticmethod
    def _select_pair(
        pool: FormulaCandidatePool,
        title_scores: list[FormulaScore],
        body_scores: list[FormulaScore],
    ) -> tuple[FormulaScore, FormulaScore] | None:
        pairs = [
            (title, body)
            for title in title_scores
            for body in body_scores
            if not pool.allowed_formula_pairs or (title.formula_code, body.formula_code) in pool.allowed_formula_pairs
        ]
        if not pairs:
            return None
        pairs.sort(key=lambda item: (-(item[0].score + item[1].score), item[0].formula_code, item[1].formula_code))
        return pairs[0]

    @staticmethod
    def _reason(score: FormulaScore, mode: str) -> str:
        matched = [key for key, value in score.score_details.items() if value > 0]
        basis = "、".join(matched) if matched else "稳定代码顺序"
        prefix = "Agent 建议经固定校验后" if mode == "agent_assisted" else "固定评分"
        return f"{prefix}选择，依据：{basis}"
