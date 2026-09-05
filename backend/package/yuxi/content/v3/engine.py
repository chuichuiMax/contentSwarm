"""V3 兼容入口；核心规则实现位于纯 Model 层。"""

from yuxi.content.model.rules.engine import (
    CombinationGroup,
    CombinationMatcher,
    MatchDecision,
    MatchRequest,
    MethodMember,
)

__all__ = ["CombinationGroup", "CombinationMatcher", "MatchDecision", "MatchRequest", "MethodMember"]
