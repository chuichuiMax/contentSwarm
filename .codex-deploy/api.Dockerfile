ARG SOURCE_IMAGE
FROM ${SOURCE_IMAGE}

# Layer the verified content-production fixes over the currently deployed API
# image so unrelated production code and dependencies remain unchanged.
COPY backend/package/yuxi/content/generation.py /app/package/yuxi/content/generation.py
COPY backend/package/yuxi/content/rules.py /app/package/yuxi/content/rules.py
COPY backend/package/yuxi/content/validators.py /app/package/yuxi/content/validators.py
COPY backend/package/yuxi/agents/skills/buildin/content-body-generator/SKILL.md /app/package/yuxi/agents/skills/buildin/content-body-generator/SKILL.md
