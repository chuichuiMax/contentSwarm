"""通用内容策略工作台领域包。"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.foundation_seed import ensure_content_foundation_seed_data
from yuxi.content.v3.agents import ensure_content_v3_agents
from yuxi.content.v3.seed import ensure_content_v3_seed_data


async def ensure_content_seed_data(db: AsyncSession) -> None:
    """幂等初始化 V3 单轨生产所需的全部配置。"""

    await ensure_content_foundation_seed_data(db)
    await ensure_content_v3_seed_data(db)
    await ensure_content_v3_agents(db)


__all__ = ["ensure_content_seed_data"]
