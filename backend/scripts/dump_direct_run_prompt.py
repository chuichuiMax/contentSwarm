import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from yuxi.content.generation import DEFAULT_DIRECT_GENERATION_PROMPT, DIRECT_TITLE_MAX_CHARS, NATURAL_CLOSING_INSTRUCTION
from yuxi.storage.postgres.manager import pg_manager

TASK_ID = "ct_35f81b1a3ff94360a0e1c9543f0a9347"


def build_direct_human_prompt(
    *,
    creative_style: dict,
    viral_source: dict,
    user_request: str,
    generation_prompt: str,
    forbidden_lexicon: dict | None = None,
) -> str:
    style = creative_style if isinstance(creative_style, dict) else {}
    style_name = str(style.get("name") or "").strip()
    style_instruction = str(style.get("instruction") or "").strip()
    style_block = (
        f"创作风格：{style_name}\n创作风格说明：{style_instruction}\n\n"
        if style_name or style_instruction
        else ""
    )
    forbidden_lexicon = forbidden_lexicon or {}
    return (
        "请根据以下输入直接创作一篇内容。返回字段 title、body、topics；"
        "不要输出解释、审核意见或额外字段。\n\n"
        "参考爆款原文的开头切入、段落顺序、信息推进和口语节奏，结尾按自然转化来写，不要照搬原文里的引流收尾。"
        "在这些位置用用户提供的事实改写，不能逐句照抄。"
        "创作风格只决定表达手法，不得因此杜撰其他人的报价、节省金额、"
        "客户经历或施工结果；原文有而用户未提供的事实，用已提供的信息自然替换或略去。"
        "用户输入的金额、单位、面积、数量及报价明细必须准确保留，不自行换算或补造。"
        f"标题不超过{DIRECT_TITLE_MAX_CHARS}个字，汉字、数字、字母、标点、单位和 Emoji 都各计 1 个字，"
        "并且必须是完整表达。正文分段使用真实换行，不要输出反斜杠和字母 n。"
        f"{NATURAL_CLOSING_INSTRUCTION}\n\n"
        f"{style_block}"
        "爆款原文：\n"
        f"{json.dumps(viral_source, ensure_ascii=False)}\n\n"
        "用户输入数据（原样保留并结合其中信息写作）：\n"
        f"{user_request}\n\n"
        "本次生成提示词：\n"
        f"{generation_prompt.strip()}\n\n"
        "排版与表情要求：\n"
        "正文要自然分段，使用适合移动端阅读的短句、空行和必要的 Markdown 排版；"
        "根据语义加入少量合适的 Emoji，位置要自然，不能堆砌或连续重复；"
        "不得用 Emoji 替代价格、数字、面积、时间、单位、品牌名或专业信息。\n\n"
        "封禁词替换表（不要在成品中解释替换过程）：\n"
        f"{json.dumps(forbidden_lexicon, ensure_ascii=False)}\n"
        "存在候选表达时选择符合上下文的写法；候选为空时改写整句，避免出现问题词。"
    )


async def main() -> None:
    async with pg_manager.get_async_session_context() as db:
        task = (
            await db.execute(
                text(
                    "SELECT id, latest_run_id, brief_json, content_type_code, runtime_config_snapshot_json "
                    "FROM content_tasks WHERE id = :id"
                ),
                {"id": TASK_ID},
            )
        ).mappings().first()
        if not task:
            print(json.dumps({"error": "TASK_NOT_FOUND"}, ensure_ascii=False))
            return
        run = None
        if task["latest_run_id"]:
            run = (
                await db.execute(
                    text(
                        "SELECT id, status, input_payload, created_at "
                        "FROM agent_runs WHERE id = :id"
                    ),
                    {"id": task["latest_run_id"]},
                )
            ).mappings().first()
        if not run:
            run = (
                await db.execute(
                    text(
                        """
                        SELECT id, status, input_payload, created_at
                        FROM agent_runs
                        WHERE thread_id = :tid AND run_type = 'content_direct'
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"tid": TASK_ID},
                )
            ).mappings().first()
        payload = (run or {}).get("input_payload") or {}
        forbidden = ((task["runtime_config_snapshot_json"] or {}).get("forbidden_lexicon") or {}).get(
            "alternatives"
        ) or {}
        user_request = str(payload.get("user_request") or "")
        generation_prompt = str(payload.get("generation_prompt") or DEFAULT_DIRECT_GENERATION_PROMPT)
        creative_style = payload.get("creative_style") or {}
        viral_source = payload.get("viral_source") or {}
        human_prompt = build_direct_human_prompt(
            creative_style=creative_style,
            viral_source=viral_source,
            user_request=user_request,
            generation_prompt=generation_prompt,
            forbidden_lexicon=forbidden,
        )
        out = {
            "task_id": task["id"],
            "content_type_code": task["content_type_code"],
            "run_id": run["id"] if run else None,
            "run_status": run["status"] if run else None,
            "model_spec": payload.get("model_spec"),
            "messages_to_model": [{"role": "human", "content": human_prompt}],
            "run_input_payload": {
                "creative_style": creative_style,
                "generation_prompt": generation_prompt,
                "viral_asset_id": payload.get("viral_asset_id"),
                "viral_source": viral_source,
                "user_request": user_request,
            },
        }
        Path("/tmp/direct-run-dump.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(out, ensure_ascii=False))


asyncio.run(main())
