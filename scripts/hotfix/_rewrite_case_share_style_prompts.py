#!/usr/bin/env python3
"""Soften case-share creative style prompts that push fine-grained visual inventory."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REPLACEMENTS = [
    (
        "结合图片中的具体设计元素/工艺细节，用大白话解答问题。",
        "结合居住感受和整体氛围用大白话解答问题，画面只点到为止，不要逐件描写构件、灯具、桌面触感。",
    ),
    (
        "严禁虚构图片中没有的东西。所有设计描述、颜色、材质必须基于图片。",
        "严禁虚构图片中没有的东西。画面只用来感受氛围和整体风格，不要逐件描写构件拼法、灯具数量、桌面触感、摆件细节。",
    ),
    (
        "用大白话描述图片中的设计细节（配色、布局、收纳等），解释为什么好，像现场给业主讲解。",
        "用大白话写整体亮点和住进去的感觉（配色、布局、收纳点到为止），解释为什么好；不要逐件细描构件、灯具、家具触感。",
    ),
    (
        "所有设计描述必须基于图片内容（如风格、颜色、家具等）。",
        "整体印象必须基于图片（如风格、色调），不要写成看图清单。",
    ),
    (
        "介绍图片背后的设计理念以及图中可见的设计元素。",
        "介绍图片背后的设计理念和住进去的感觉；画面元素点到为止，不要逐一展开。",
    ),
    (
        '设计元素展开**：逐一介绍图片中可见的具体设计元素（颜色、材质、造型、家具等）。每个元素一句话带出它和理念的关联（如"墙面用浅灰乳胶漆，配合无主灯，视觉上把墙往后推，空间显大"）。',
        "整体印象**：最多点到一两处硬装整体印象，并一句话带出和理念的关联；不要逐件描写颜色材质清单、灯具数量或家具触感。",
    ),
    (
        "设计元素展开**：介绍图片可见的具体元素（颜色、材质、造型、家具等）。每个元素一句话带出和理念的关联。专业术语（如无主灯、木饰面、岩板上墙、错缝拼接等）直接用，不硬翻译。",
        "整体印象**：最多点到一两处硬装整体印象，并一句话带出和理念的关联；不要逐件展开。专业术语可直接用，不硬翻译。",
    ),
    (
        "所有设计元素必须基于图片内容。理念可以解读，但不能凭空编造图片里不存在的造型、颜色或材质。",
        "整体印象必须基于图片。理念可以解读，但不能凭空编造，也不要逐件细描画面。",
    ),
]


def rewrite_prompt(prompt: str) -> tuple[str, int]:
    text = prompt
    hits = 0
    for old, new in REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
            hits += 1
    return text, hits


def main() -> None:
    # Seed used by MP / local defaults
    path1 = ROOT / "backend/package/yuxi/content/data/content_style_prompts.json"
    data1 = json.loads(path1.read_text(encoding="utf-8"))
    total = 0
    for item in data1.get("装修案例分享") or []:
        item["prompt"], hits = rewrite_prompt(item.get("prompt") or "")
        total += hits
    path1.write_text(json.dumps(data1, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("content_style_prompts.json hits", total)

    path2 = ROOT / "backend/scripts/_creative_styles_2.0.json"
    data2 = json.loads(path2.read_text(encoding="utf-8"))
    total2 = 0
    for item in data2:
        if "案例" not in str(item.get("content_type_name") or ""):
            continue
        item["prompt"], hits = rewrite_prompt(item.get("prompt") or "")
        total2 += hits
    path2.write_text(json.dumps(data2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("_creative_styles_2.0.json hits", total2)


if __name__ == "__main__":
    main()
