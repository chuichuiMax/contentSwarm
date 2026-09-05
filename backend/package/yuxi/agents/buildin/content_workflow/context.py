from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from yuxi.agents.context import BaseContext


@dataclass(kw_only=True)
class ContentWorkflowContext(BaseContext):
    task_id: str = field(default="", metadata={"configurable": False, "hide": True})
    workflow_definition: dict[str, Any] = field(default_factory=dict, metadata={"configurable": False, "hide": True})
    rule_bundle: dict[str, Any] = field(default_factory=dict, metadata={"configurable": False, "hide": True})
    model_spec: str | None = field(default=None, metadata={"configurable": False, "hide": True})
