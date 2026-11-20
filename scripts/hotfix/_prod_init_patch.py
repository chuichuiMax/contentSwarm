from pathlib import Path

p = Path("/opt/contentSwarm/backend/server/routers/__init__.py")
text = p.read_text(encoding="utf-8")
for line in (
    "from server.routers.creative_style_router import creative_styles\n",
    "from server.routers.material_audience_router import material_audiences\n",
    "router.include_router(creative_styles)  # /api/creative-styles/* 创作风格管理\n",
    "router.include_router(material_audiences)  # /api/material-audiences/* 企业图库受众\n",
):
    text = text.replace(line, "")
p.write_text(text, encoding="utf-8")
print("ok")