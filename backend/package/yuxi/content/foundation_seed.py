"""内容 V3 依赖的渠道、词库和合规基础数据。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.catalog import (
    DECORATION_LEXICON_CATEGORIES,
    INDUSTRY_CONFIG,
    XHS_CHANNEL_PROFILE_ID,
    XHS_CHANNEL_VERSION_ID,
)
from yuxi.content.rules import INDUSTRIES
from yuxi.storage.postgres.models_content import (
    ChannelProfile,
    ChannelProfileVersion,
    CompliancePolicyVersion,
    LexiconEntry,
    LexiconPack,
    LexiconVersion,
    ReplacementRule,
)
from yuxi.utils.datetime_utils import utc_now_naive


async def ensure_content_foundation_seed_data(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": "yuxi_content_foundation_seed"},
    )
    now = utc_now_naive()
    await _ensure_channel(db, now)
    await _ensure_lexicons(db, now)
    await _ensure_compliance(db, now)
    await db.commit()


async def _ensure_channel(db: AsyncSession, now) -> None:
    if await db.get(ChannelProfile, XHS_CHANNEL_PROFILE_ID) is None:
        db.add(
            ChannelProfile(
                id=XHS_CHANNEL_PROFILE_ID,
                code="xiaohongshu",
                name="小红书",
                connector_type="xiaohongshu",
                created_at=now,
            )
        )
        await db.flush()
    if await db.get(ChannelProfileVersion, XHS_CHANNEL_VERSION_ID) is None:
        db.add(
            ChannelProfileVersion(
                id=XHS_CHANNEL_VERSION_ID,
                profile_id=XHS_CHANNEL_PROFILE_ID,
                version=1,
                status="published",
                title_constraints={"min_length": 6, "max_length": 20, "emoji_allowed": True},
                body_constraints={"min_length": 100, "max_length": 1000, "emoji_allowed": True},
                topic_constraints={"min_count": 1, "max_count": 10},
                media_constraints={"min_count": 1, "max_count": 18, "ratios": ["3:4", "1:1"]},
                cta_policy={"allowed": ["收藏", "评论", "私信了解"], "forbidden": ["强制关注"]},
                link_policy={"external_link": "blocked", "contact_info": "confirm"},
                preview_schema={"type": "xiaohongshu-note"},
                connector_config_ref="xiaohongshu",
                created_by="system",
                created_at=now,
                published_at=now,
            )
        )


async def _ensure_lexicon(
    db: AsyncSession,
    *,
    pack_id: str,
    version_id: str,
    code: str,
    scope_type: str,
    scope_id: str | None,
    name: str,
    category: str,
    description: str,
    entries: list[str],
    now,
) -> None:
    if await db.get(LexiconPack, pack_id) is None:
        db.add(
            LexiconPack(
                id=pack_id,
                code=code,
                scope_type=scope_type,
                scope_id=scope_id,
                tenant_id=None,
                name=name,
                semantic_category=category,
                description=description,
                created_by="system",
                created_at=now,
            )
        )
        await db.flush()
    if await db.get(LexiconVersion, version_id) is None:
        db.add(
            LexiconVersion(
                id=version_id,
                pack_id=pack_id,
                version=1,
                status="published",
                changelog="V3 基础词库",
                source_metadata={"source": "builtin-v3"},
                created_by="system",
                created_at=now,
                published_at=now,
            )
        )
        await db.flush()
    for order, value in enumerate(entries, 1):
        entry_prefix = (
            scope_id if scope_type == "industry" and scope_id != "decoration" else pack_id.removeprefix("lexicon-")
        )
        entry_id = f"entry-{entry_prefix}-{order}"
        if await db.get(LexiconEntry, entry_id) is None:
            db.add(
                LexiconEntry(
                    id=entry_id,
                    version_id=version_id,
                    text=value,
                    normalized_text=value.lower(),
                    tags=[scope_id or category, category],
                    risk_level="safe",
                    applicable_formula_codes=[],
                    applicable_slot_keys=[category] if scope_type == "platform" else [],
                    enabled=True,
                    sort_order=order,
                )
            )


async def _ensure_lexicons(db: AsyncSession, now) -> None:
    platform_lexicons = {
        "emotion": ["没想到", "真实体验", "终于讲清楚"],
        "suspense": ["很多人忽略了", "真正拉开差距的是", "先别急着决定"],
        "advice": ["建议先看清这一点", "做决定前先确认", "别只看表面"],
        "call_to_action": ["收藏备用", "按这份清单检查", "先记住这几步"],
    }
    for category, entries in platform_lexicons.items():
        await _ensure_lexicon(
            db,
            pack_id=f"lexicon-platform-{category}",
            version_id=f"lexicon-platform-{category}-v1",
            code=f"platform-{category}",
            scope_type="platform",
            scope_id=None,
            name=f"平台{category}表达",
            category=category,
            description="跨行业固定表达",
            entries=entries,
            now=now,
        )

    industries = {item["slug"]: item for item in INDUSTRIES}
    for slug, config in INDUSTRY_CONFIG.items():
        if slug == "decoration":
            for order, category in enumerate(DECORATION_LEXICON_CATEGORIES, 1):
                await _ensure_lexicon(
                    db,
                    pack_id=f"lexicon-decoration-{order:02d}",
                    version_id=f"lexicon-decoration-{order:02d}-v1",
                    code=f"decoration-{order:02d}",
                    scope_type="industry",
                    scope_id=slug,
                    name=f"装修细分词库·{category}",
                    category=category,
                    description="装修与家居行业包专用",
                    entries=[category],
                    now=now,
                )
        else:
            await _ensure_lexicon(
                db,
                pack_id=f"lexicon-{slug}-core",
                version_id=f"lexicon-{slug}-core-v1",
                code=f"{slug}-core",
                scope_type="industry",
                scope_id=slug,
                name=f"{industries[slug]['name']}核心表达",
                category="industry",
                description="行业专用表达",
                entries=config["terms"],
                now=now,
            )


async def _ensure_compliance(db: AsyncSession, now) -> None:
    policies = [
        (
            "compliance-platform-v1",
            "platform",
            None,
            "平台基础事实合规",
            {"unsupported_numbers": "block", "unsupported_promises": "block"},
        ),
        ("compliance-xiaohongshu-v1", "channel", "xiaohongshu", "小红书渠道合规", {"external_link": "block"}),
        ("compliance-decoration-v1", "industry", "decoration", "装修与家居合规", {"price_promise": "confirm"}),
    ]
    for policy_id, scope_type, scope_id, name, config in policies:
        if await db.get(CompliancePolicyVersion, policy_id) is None:
            db.add(
                CompliancePolicyVersion(
                    id=policy_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    tenant_id=None,
                    version=1,
                    status="published",
                    name=name,
                    policy_config=config,
                    created_by="system",
                    created_at=now,
                    published_at=now,
                )
            )
    await db.flush()
    rows = [
        ("ABSOLUTE_GUARANTEE", "compliance-platform-v1", "百分百保证", "block", None, True, "禁止无证据的绝对承诺"),
        (
            "BEST_CLAIM",
            "compliance-platform-v1",
            "全网最好",
            "replace",
            "更适合具体需求",
            False,
            "绝对化比较需要改为可验证表达",
        ),
        ("EXTERNAL_LINK", "compliance-xiaohongshu-v1", r"https?://\S+", "block", None, True, "小红书正文不允许外链"),
        (
            "CONTACT_INFO",
            "compliance-xiaohongshu-v1",
            r"(?:微信|VX|手机号)[:：]?\s*[A-Za-z0-9_-]+",
            "confirm",
            None,
            True,
            "联系方式属于导流表达，需要人工确认",
        ),
        (
            "ZERO_ADDITION",
            "compliance-decoration-v1",
            "零增项",
            "confirm",
            None,
            True,
            "零增项必须明确合同范围并人工确认",
        ),
        ("GREEN_PROMISE", "compliance-decoration-v1", "百分百环保", "block", None, True, "环保绝对承诺不可发布"),
    ]
    for order, (code, policy_id, pattern, action, replacement, confirm, explanation) in enumerate(rows, 1):
        rule_id = f"replacement-{code.lower()}"
        if await db.get(ReplacementRule, rule_id) is None:
            db.add(
                ReplacementRule(
                    id=rule_id,
                    policy_version_id=policy_id,
                    rule_code=code,
                    pattern=pattern,
                    match_type="regex" if pattern.startswith(("http", "(?:")) else "literal",
                    risk_level="high" if action == "block" else "warning",
                    action=action,
                    replacement=replacement,
                    human_confirmation_required=confirm,
                    explanation=explanation,
                    enabled=True,
                    sort_order=order,
                )
            )


__all__ = ["ensure_content_foundation_seed_data"]
