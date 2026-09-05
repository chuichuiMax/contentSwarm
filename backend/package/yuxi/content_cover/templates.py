from __future__ import annotations

COVER_SIZES = {
    "1080x1440": {"width": 1080, "height": 1440, "label": "小红书 3:4"},
    "1080x1080": {"width": 1080, "height": 1080, "label": "方形 1:1"},
}

COVER_THEMES = {
    "editorial_ink": {
        "id": "editorial_ink",
        "name": "编辑墨色",
        "background": "#F4EFE6",
        "foreground": "#1B1A18",
        "accent": "#9F2D20",
        "surface": "#FFFDF8",
    },
    "swiss_accent": {
        "id": "swiss_accent",
        "name": "瑞士强调色",
        "background": "#F5F5F2",
        "foreground": "#111111",
        "accent": "#1746D1",
        "surface": "#FFFFFF",
    },
}

COVER_TEMPLATES = {
    "grid_3x3": {"id": "grid_3x3", "name": "九宫格", "min_assets": 2, "max_assets": 9},
    "split_vertical": {"id": "split_vertical", "name": "左右分割", "min_assets": 2, "max_assets": 2},
    "split_horizontal": {"id": "split_horizontal", "name": "上下分割", "min_assets": 2, "max_assets": 2},
    "before_after": {"id": "before_after", "name": "前后对比", "min_assets": 2, "max_assets": 2},
    "card_stack": {"id": "card_stack", "name": "卡片式", "min_assets": 2, "max_assets": 4},
    "hero_thumbs": {"id": "hero_thumbs", "name": "主图 + 辅图", "min_assets": 2, "max_assets": 5},
}
