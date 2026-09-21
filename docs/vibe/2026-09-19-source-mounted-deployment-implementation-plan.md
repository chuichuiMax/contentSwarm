# GitLab 源码挂载部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一套不把业务源码或业务产物打入运行镜像的生产部署模式，使运维在基础镜像准备完成后，只需从 GitLab 拉取代码并执行固定 Compose 流程即可发布 Python、Vue 和 HyCanvas 功能更新。

**Architecture:** Python 服务从宿主机只读挂载源码并在启动前校验基础镜像依赖摘要；单个 Release Builder 使用固定 Node/Go 工具链镜像，从只读源码构建 Web 静态文件和 HyCanvas 二进制到版本化宿主机目录，然后原子切换 `current`。运行容器只使用固定基础镜像和宿主机挂载，持久化数据、配置、源码、缓存和发布产物分别位于外部目录。

**Tech Stack:** Docker Engine 24+、Docker Compose 2.20+、Bash、Python 3.12、uv、Vue/Vite、Node 24、pnpm 10.11、Go 1.25、Nginx、pytest。

**Spec:** `docs/vibe/2026-09-19-source-mounted-deployment-design.md`

## Global Constraints

- 普通业务发布不得执行 `docker build`，不得产生或传输新的业务 Docker 镜像。
- API、Worker、XHS Gateway 和 Sandbox Provisioner 必须执行只读挂载的宿主机源码，不得从基础镜像取得业务源码。
- Web 和 HyCanvas 运行容器不得挂载源码，只挂载 Release Builder 生成的版本化业务产物。
- 生产服务不得使用 Vite dev server、Uvicorn reload、watchfiles 或 Air。
- 基础镜像必须使用固定版本标签或 digest，禁止 `latest`。
- Python 基础镜像与两层 uv lock 摘要不一致时必须在服务启动前失败。
- `.env.prod`、持久化数据、构建缓存和发布产物必须位于 Git 工作区外。
- 现有 `docker-compose.yml` 和 `docker-compose.prod.yml` 保持兼容，源码部署使用独立的 `docker-compose.source.yml`。
- 新增后端测试放在 `backend/test/unit/deployment`；所有开发和验证优先在 Docker 容器中执行。
- 代码、脚本和配置挂载只读；只有数据、缓存和 Release 根目录允许写入。

## Review Focus

- Git 工作区包含未提交的已跟踪文件时，发布必须在停止业务服务前失败，不能覆盖或发布本地修改；Task 5 的脚本测试覆盖。
- Python 任一 lock 文件变化而基础镜像未更新时，API、Worker 和 Gateway 必须全部拒绝启动；Task 1 的摘要测试和 Task 4 的 Compose 契约测试覆盖。
- Web 成功但 HyCanvas 构建失败时，`current` 必须保持旧版本，不能发布半套产物；Task 3 的失败原子性测试覆盖。
- 首次部署尚无 `current`/`previous` 链接时，Builder 和运行服务必须形成合法首版；Task 3 的首次发布测试覆盖。
- 新版本启动后健康检查失败时，代码提交和 Web/HyCanvas 发布指针必须共同恢复；Task 5 的回滚测试覆盖。

---

## 文件结构与职责

### 新增文件

- `docker-compose.source.yml`：源码部署的唯一 Compose 入口，声明基础设施、Builder、源码挂载业务服务和产物运行服务。
- `docker/runtime/api-base.Dockerfile`：生成不含业务源码的 API/Worker/Gateway 基础镜像。
- `docker/runtime/sandbox-base.Dockerfile`：生成不含 `app.py` 的 Sandbox Provisioner 基础镜像。
- `docker/runtime/web-builder-base.Dockerfile`：固定 Node 24 和 pnpm 10.11 的 Web 构建基础层。
- `docker/runtime/hycanvas-builder-base.Dockerfile`：在 Web Builder 基础上增加 Go 1.25，作为统一 Release Builder 镜像。
- `docker/runtime/hycanvas-runtime-base.Dockerfile`：不含业务二进制的 HyCanvas 运行基础镜像。
- `scripts/source-deploy/check-runtime-lock.sh`：写入或校验 Python 依赖摘要。
- `scripts/source-deploy/build-release.sh`：从只读源码构建完整版本目录并原子切换发布链接。
- `scripts/source-deploy/deploy-source.sh`：校验、停止源码服务、拉取、构建、重建、健康检查及失败回滚。
- `scripts/source-deploy/rollback-source.sh`：显式恢复到记录的上一 Git SHA 和上一产物版本。
- `.env.source.template`：源码部署专用路径、基础镜像与端口变量模板。
- `docs/advanced/source-deployment.md`：面向运维的首次部署、更新、依赖升级、回滚、日志和备份说明。
- `backend/test/unit/deployment/test_runtime_lock_contract.py`：锁摘要脚本和 API 基础镜像契约测试。
- `backend/test/unit/deployment/test_source_runtime_images.py`：其余基础镜像无业务代码契约测试。
- `backend/test/unit/deployment/test_release_builder.py`：Builder 首次发布、重复发布和失败原子性测试。
- `backend/test/unit/deployment/test_source_compose_contract.py`：Compose 服务、挂载、命令和数据目录契约测试。
- `backend/test/unit/deployment/test_source_deploy_scripts.py`：部署顺序、工作区保护和回滚测试。

### 修改文件

- `.gitignore`：忽略源码部署本地状态、发布目录和缓存目录，但不忽略模板与脚本。
- `docs/.vitepress/config.mts`：在高级文档导航中增加源码挂载部署指南。
- `docs/develop-guides/changelog.md`：记录新的部署模式、限制和回退路径。

## Task 1：API 基础镜像与依赖摘要门禁

**Files:**
- Create: `docker/runtime/api-base.Dockerfile`
- Create: `scripts/source-deploy/check-runtime-lock.sh`
- Create: `backend/test/unit/deployment/test_runtime_lock_contract.py`

**Interfaces:**
- Consumes: `backend/uv.lock`、`backend/package/uv.lock`、现有 `docker/api.Dockerfile` 的系统依赖清单。
- Produces: `contentswarm-api-runtime:${API_RUNTIME_VERSION}`；命令 `check-runtime-lock.sh write <output> <lock>...` 和 `check-runtime-lock.sh verify <expected> <lock>...`。

- [ ] **Step 1: 编写锁摘要脚本失败测试**

```python
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[4]
SCRIPT = Path(__file__).parents[4] / "scripts/source-deploy/check-runtime-lock.sh"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_runtime_lock_round_trip_and_mismatch(tmp_path: Path):
    root_lock = tmp_path / "root.lock"
    package_lock = tmp_path / "package.lock"
    expected = tmp_path / "expected.sha256"
    root_lock.write_text("root-v1", encoding="utf-8")
    package_lock.write_text("package-v1", encoding="utf-8")

    assert run_script("write", str(expected), str(root_lock), str(package_lock)).returncode == 0
    assert run_script("verify", str(expected), str(root_lock), str(package_lock)).returncode == 0

    package_lock.write_text("package-v2", encoding="utf-8")
    result = run_script("verify", str(expected), str(root_lock), str(package_lock))
    assert result.returncode != 0
    assert "基础镜像依赖摘要不匹配" in result.stderr
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_runtime_lock_contract.py -v
```

Expected: FAIL，原因是 `check-runtime-lock.sh` 不存在。

- [ ] **Step 3: 实现确定性的摘要脚本**

脚本必须使用 POSIX/Bash 严格模式，按参数顺序计算每个 lock 文件的 SHA-256，再对摘要列表计算组合 SHA-256。`write` 原子写入目标文件；`verify` 使用 `cmp` 比较且不输出摘要内容以外的敏感信息。

```bash
#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
digest_file="${2:-}"
shift 2

calculate_digest() {
  for lock_file in "$@"; do
    [[ -f "$lock_file" ]] || { echo "缺少依赖锁文件: $lock_file" >&2; return 1; }
    sha256sum "$lock_file" | awk '{print $1}'
  done | sha256sum | awk '{print $1}'
}
```

完成 `write`、`verify`、参数数量校验和原子临时文件替换。错误文案包含“基础镜像依赖摘要不匹配”。

- [ ] **Step 4: 编写 API 基础镜像契约测试**

```python
def test_api_runtime_final_stage_has_no_business_source_copy():
    dockerfile = (ROOT / "docker/runtime/api-base.Dockerfile").read_text(encoding="utf-8")
    final_stage = dockerfile.split("FROM system AS runtime", maxsplit=1)[1]
    assert "COPY backend/server" not in final_stage
    assert "COPY backend/package /app/package" not in final_stage
    assert "/opt/runtime-locks/api.sha256" in final_stage
    assert "patchright install --with-deps chromium" in dockerfile
```

- [ ] **Step 5: 实现多阶段 API 基础镜像**

以现有 `docker/api.Dockerfile` 为依赖事实来源：

1. `system` 阶段安装 Python、uv、Node、curl、ffmpeg、git、libpq、字体和图形运行库。
2. `deps` 阶段复制 `backend/pyproject.toml`、两层 lock、`backend/package`，执行现有 frozen uv sync。
3. 在 `deps` 阶段调用 `check-runtime-lock.sh write` 生成 `/opt/runtime-locks/api.sha256`。
4. `runtime` 阶段只复制 Python 环境、浏览器运行时和摘要文件，不复制 `/build/backend/server` 或 `/build/backend/package`。
5. 保留 `PATH`、`TZ` 和 `UV_PROJECT_ENVIRONMENT`，不设置开发 reload 命令。

中间依赖阶段可以读取业务包元数据和源码以解析 editable 依赖，但最终镜像必须通过容器检查证明不存在 `/app/server` 和 `/app/package/yuxi`。

- [ ] **Step 6: 运行任务测试**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_runtime_lock_contract.py -v
```

Expected: PASS。

- [ ] **Step 7: 构建并检查 API 基础镜像**

Run:

```bash
docker build -f docker/runtime/api-base.Dockerfile -t contentswarm-api-runtime:test .
docker run --rm contentswarm-api-runtime:test sh -lc \
  'test ! -e /app/server && test ! -e /app/package/yuxi && test -f /opt/runtime-locks/api.sha256'
```

Expected: 两条命令均返回 0。

- [ ] **Step 8: 提交**

```bash
git add docker/runtime/api-base.Dockerfile scripts/source-deploy/check-runtime-lock.sh backend/test/unit/deployment/test_runtime_lock_contract.py
git commit -m "feat(deploy): 增加无源码 API 基础镜像"
```

## Task 2：其余 Builder 与 Runtime 基础镜像

**Files:**
- Create: `docker/runtime/sandbox-base.Dockerfile`
- Create: `docker/runtime/web-builder-base.Dockerfile`
- Create: `docker/runtime/hycanvas-builder-base.Dockerfile`
- Create: `docker/runtime/hycanvas-runtime-base.Dockerfile`
- Create: `backend/test/unit/deployment/test_source_runtime_images.py`

**Interfaces:**
- Consumes: `docker/sandbox_provisioner/requirements.txt`、Node 24、pnpm 10.11、Go 1.25 和现有 HyCanvas Runtime 系统包。
- Produces: `contentswarm-sandbox-runtime:${SANDBOX_RUNTIME_VERSION}`、`contentswarm-web-builder:${WEB_BUILDER_VERSION}`、`contentswarm-release-builder:${RELEASE_BUILDER_VERSION}`、`contentswarm-hycanvas-runtime:${HYCANVAS_RUNTIME_VERSION}`。

- [ ] **Step 1: 编写基础镜像边界测试**

```python
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[4]


@pytest.mark.parametrize(
    ("path", "forbidden"),
    [
        ("docker/runtime/sandbox-base.Dockerfile", ["COPY docker/sandbox_provisioner/app.py"]),
        ("docker/runtime/web-builder-base.Dockerfile", ["COPY web", "COPY ./web"]),
        ("docker/runtime/hycanvas-builder-base.Dockerfile", ["COPY apps/hycanvas"]),
        ("docker/runtime/hycanvas-runtime-base.Dockerfile", ["COPY --from=", "COPY apps/hycanvas"]),
    ],
)
def test_runtime_dockerfiles_do_not_copy_business_sources(path: str, forbidden: list[str]):
    content = (ROOT / path).read_text(encoding="utf-8")
    for fragment in forbidden:
        assert fragment not in content
```

另断言 Node、pnpm、Go、ffmpeg、字体和 curl 使用设计规格中的固定版本/包。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_source_runtime_images.py -v
```

Expected: FAIL，四个 Dockerfile 尚不存在。

- [ ] **Step 3: 实现 Sandbox 基础镜像**

从 `python:3.12-slim` 安装 `requirements.txt` 和 curl，仅复制依赖清单；不得复制 `app.py` 或 `sandbox.env`。增加对 `/health` 所需 Python 库，不在镜像中定义业务入口脚本。

- [ ] **Step 4: 实现 Web Builder 基础镜像**

从 `node:24-bookworm` 安装固定 `pnpm@10.11.0`、git、ca-certificates 和 rsync。镜像只提供工具链，工作目录为 `/work`。

- [ ] **Step 5: 实现统一 Release Builder 基础镜像**

`hycanvas-builder-base.Dockerfile` 以 Web Builder 镜像为 `ARG WEB_BUILDER_BASE` 的父镜像，安装 Go 1.25 工具链。最终统一 Builder 同时拥有 Node、pnpm、npm 和 Go，供单个 `release-builder` 服务构建 Web 与 HyCanvas。

- [ ] **Step 6: 实现 HyCanvas Runtime 基础镜像**

从固定 Debian Bookworm Slim 安装 ffmpeg、ca-certificates、curl、fonts-noto-cjk；创建 `/app` 和 `/app/.data/storage`，但不复制 HyCanvas 二进制。入口由 Compose 指定挂载路径 `/app/hycanvas`。

- [ ] **Step 7: 运行单元契约和镜像烟测**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_source_runtime_images.py -v
docker build -f docker/runtime/sandbox-base.Dockerfile -t contentswarm-sandbox-runtime:test .
docker build -f docker/runtime/web-builder-base.Dockerfile -t contentswarm-web-builder:test .
docker build --build-arg WEB_BUILDER_BASE=contentswarm-web-builder:test -f docker/runtime/hycanvas-builder-base.Dockerfile -t contentswarm-release-builder:test .
docker build -f docker/runtime/hycanvas-runtime-base.Dockerfile -t contentswarm-hycanvas-runtime:test .
docker run --rm contentswarm-hycanvas-runtime:test sh -lc 'test ! -e /app/hycanvas'
```

Expected: 全部返回 0。

- [ ] **Step 8: 提交**

```bash
git add docker/runtime backend/test/unit/deployment/test_source_runtime_images.py
git commit -m "feat(deploy): 增加源码部署基础镜像"
```

## Task 3：版本化 Release Builder 与原子切换

**Files:**
- Create: `scripts/source-deploy/build-release.sh`
- Create: `backend/test/unit/deployment/test_release_builder.py`

**Interfaces:**
- Consumes: `SOURCE_ROOT`、`RELEASE_ROOT`、`CACHE_ROOT`、`VITE_BASE_PATH`、`CONTENTSWARM_PUBLIC_URL`，以及挂载源码中的 Git SHA。
- Produces: `${RELEASE_ROOT}/<git-sha>/web-dist`、`${RELEASE_ROOT}/<git-sha>/hycanvas`、`${RELEASE_ROOT}/<git-sha>/manifest.env`、原子链接 `current` 和 `previous`。

- [ ] **Step 0: 建立真实 Git 与假工具链测试夹具**

测试夹具必须真实执行 Git 和发布脚本，只替换耗时的包管理器/编译器。测试文件先实现以下公共函数，后续测试直接复用：

```python
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).parents[4]
SCRIPT = ROOT / "scripts/source-deploy/build-release.sh"


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def git_head(repo: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def commit_change(repo: Path, value: str) -> None:
    marker = repo / "release-marker.txt"
    marker.write_text(value, encoding="utf-8")
    assert run(["git", "add", "release-marker.txt"], repo).returncode == 0
    assert run(["git", "commit", "-m", value], repo).returncode == 0


def arrange_fake_release_workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    source = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    release_root = tmp_path / "releases"
    work_root = tmp_path / "work"
    fake_bin.mkdir()
    (source / "web").mkdir(parents=True)
    (source / "apps/hycanvas/backend/internal/webui").mkdir(parents=True)
    (source / "apps/hycanvas/frontend").mkdir(parents=True)
    (source / "web/package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (source / "web/pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")
    (source / "apps/hycanvas/package.json").write_text('{"scripts":{}}', encoding="utf-8")
    (source / "apps/hycanvas/package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")

    write_executable(fake_bin / "pnpm", """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "run build" ]]; then mkdir -p dist; printf web > dist/index.html; fi
""")
    write_executable(fake_bin / "npm", """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"build:dist"* ]]; then mkdir -p frontend/out; printf hycanvas-web > frontend/out/index.html; fi
""")
    write_executable(fake_bin / "go", """#!/usr/bin/env bash
set -euo pipefail
[[ "${FAKE_GO_EXIT:-0}" == 0 ]] || exit "$FAKE_GO_EXIT"
output=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then output="$2"; shift 2; else shift; fi
done
mkdir -p "$(dirname "$output")"
printf '#!/usr/bin/env sh\nexit 0\n' > "$output"
chmod 0755 "$output"
""")

    assert run(["git", "init"], source).returncode == 0
    assert run(["git", "config", "user.email", "test@example.com"], source).returncode == 0
    assert run(["git", "config", "user.name", "Source Deploy Test"], source).returncode == 0
    assert run(["git", "add", "."], source).returncode == 0
    assert run(["git", "commit", "-m", "initial"], source).returncode == 0

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "WORK_ROOT": str(work_root),
            "CACHE_ROOT": str(tmp_path / "cache"),
            "VITE_BASE_PATH": "/boyun/",
            "CONTENTSWARM_PUBLIC_URL": "https://content.example.test/boyun",
        }
    )
    return source, release_root, env


def run_builder(source: Path, release_root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    invocation_env = env | {"SOURCE_ROOT": str(source), "RELEASE_ROOT": str(release_root)}
    return run(["bash", str(SCRIPT)], source, invocation_env)
```

- [ ] **Step 1: 编写首次发布测试**

测试使用临时 Git 仓库和 PATH 中的假 `pnpm`、`npm`、`go` 命令。假命令只生成 Builder 预期的 `dist/index.html`、HyCanvas 前端输出和可执行二进制，不绕过脚本的 Git、目录和原子切换逻辑。

```python
def test_first_release_creates_version_and_current_link(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)
    result = run_builder(source, release_root, env)
    git_sha = git_head(source)

    assert result.returncode == 0, result.stderr
    version = release_root / git_sha
    assert (version / "web-dist/index.html").is_file()
    assert (version / "hycanvas").is_file()
    assert (version / "manifest.env").is_file()
    assert (release_root / "current").resolve() == version.resolve()
    assert not (release_root / "previous").exists()
```

- [ ] **Step 2: 编写构建失败不切换测试**

```python
def test_hycanvas_failure_keeps_previous_release(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)
    assert run_builder(source, release_root, env).returncode == 0
    old_current = (release_root / "current").resolve()

    commit_change(source, "second")
    env["FAKE_GO_EXIT"] = "19"
    result = run_builder(source, release_root, env)

    assert result.returncode == 19
    assert (release_root / "current").resolve() == old_current
    assert not (release_root / git_head(source)).exists()
```

- [ ] **Step 3: 编写重复发布和脏工作区测试**

断言同一 Git SHA 且 manifest 完整时复用；已跟踪文件变脏时在创建 staging 目录前失败，并输出“工作区存在未提交的已跟踪文件”。

- [ ] **Step 4: 运行测试并确认失败**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_release_builder.py -v
```

Expected: FAIL，Builder 脚本不存在。

- [ ] **Step 5: 实现 Release Builder**

脚本必须：

1. 校验所有根目录为绝对路径且 `RELEASE_ROOT` 不位于 Git 工作区内。
2. 通过 `git status --porcelain --untracked-files=no` 拒绝已跟踪修改。
3. 将 Web 和 HyCanvas 分别复制到容器可写的 `${WORK_ROOT}` 后构建，绝不写入只读源码挂载。
4. Web 执行 `pnpm install --frozen-lockfile` 和 `pnpm run build`。
5. HyCanvas 执行 `npm ci`、`npm run build:packages`、`npm run build:dist -w frontend`，将前端输出同步到 Go embed 目录，再执行 `CGO_ENABLED=0 go build -tags embed`。
6. 所有输出先写入 `${RELEASE_ROOT}/.<git-sha>.tmp`。
7. 计算 Web 文件树和 HyCanvas 二进制摘要，写入 manifest。
8. 完整校验后重命名到 `${RELEASE_ROOT}/<git-sha>`。
9. 使用 `current.next`、`previous.next` 和 `mv -Tf` 原子切换相对符号链接。
10. 使用 `trap` 只删除本次明确创建且位于 `RELEASE_ROOT` 下的临时目录。

- [ ] **Step 6: 运行 Builder 单元测试**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_release_builder.py -v
```

Expected: PASS。

- [ ] **Step 7: 用真实工具链执行构建烟测**

Run:

```bash
docker run --rm \
  -e SOURCE_ROOT=/source \
  -e RELEASE_ROOT=/releases \
  -e CACHE_ROOT=/cache \
  -e VITE_BASE_PATH=/boyun/ \
  -e CONTENTSWARM_PUBLIC_URL=http://127.0.0.1:8090/boyun \
  -v "$PWD:/source:ro" \
  -v "$PWD/.tmp/source-release-test:/releases" \
  -v contentswarm-source-pnpm:/cache/pnpm \
  -v contentswarm-source-npm:/cache/npm \
  -v contentswarm-source-go-mod:/cache/go-mod \
  -v contentswarm-source-go-build:/cache/go-build \
  contentswarm-release-builder:test \
  bash /source/scripts/source-deploy/build-release.sh
```

Expected: 生成当前 Git SHA 对应目录、`current` 链接、Web 入口和可执行 HyCanvas 文件。验证后只删除 `.tmp/source-release-test` 这一明确测试目录。

- [ ] **Step 8: 提交**

```bash
git add scripts/source-deploy/build-release.sh backend/test/unit/deployment/test_release_builder.py
git commit -m "feat(deploy): 增加原子业务产物构建"
```

## Task 4：源码部署 Compose

**Files:**
- Create: `docker-compose.source.yml`
- Create: `.env.source.template`
- Create: `backend/test/unit/deployment/test_source_compose_contract.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Tasks 1-3 的基础镜像、锁摘要脚本和 Builder；现有生产 Compose 的环境变量、网络、健康检查和依赖关系。
- Produces: `docker compose --env-file <external-env> -f docker-compose.source.yml` 入口。

- [ ] **Step 1: 编写 Compose 契约测试**

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[4]


def test_business_services_use_images_without_build_and_read_only_sources():
    compose = yaml.safe_load((ROOT / "docker-compose.source.yml").read_text(encoding="utf-8"))
    for name in ("api", "worker", "xhs-browser-gateway", "sandbox-provisioner", "web", "hycanvas-app"):
        assert "build" not in compose["services"][name]

    api_volumes = compose["services"]["api"]["volumes"]
    assert "${SOURCE_ROOT}/backend/server:/app/server:ro" in api_volumes
    assert "${SOURCE_ROOT}/backend/package:/app/package:ro" in api_volumes
    assert all("--reload" not in str(compose["services"][name].get("command", "")) for name in ("api", "worker", "xhs-browser-gateway"))
```

补充断言：

- 三个 Python 服务启动前调用 lock verifier。
- `release-builder` 只读挂载 SOURCE_ROOT，可写挂载 RELEASE_ROOT/CACHE_ROOT。
- Web 只挂载 `current/web-dist`，HyCanvas 只挂载 `current/hycanvas`。
- 所有数据库卷使用 `${DATA_ROOT}`。
- `.env.prod` 使用 `${CONFIG_ROOT}`。
- MinerU/PaddleX 仍位于 `all` profile。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_source_compose_contract.py -v
```

Expected: FAIL，源码部署 Compose 不存在。

- [ ] **Step 3: 建立 Compose 公共锚点和环境变量**

从 `docker-compose.prod.yml` 复制并保留 `x-api-worker-env`。增加：

```yaml
x-api-source-volumes: &api-source-volumes
  - ${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/server:/app/server:ro
  - ${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/package:/app/package:ro
  - ${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/uv.lock:/opt/source-locks/backend.uv.lock:ro
  - ${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/package/uv.lock:/opt/source-locks/package.uv.lock:ro
  - ${SOURCE_ROOT:?SOURCE_ROOT must be set}/scripts/source-deploy:/opt/source-deploy:ro
  - ${DATA_ROOT:?DATA_ROOT must be set}/yuxi:/app/saves
  - ${DATA_ROOT:?DATA_ROOT must be set}/models:/app/models
```

避免 YAML 锚点与服务特有卷合并造成覆盖；如 Compose 序列合并不清晰，则在三个服务中显式重复这组卷，优先可读性。

- [ ] **Step 4: 实现 Python 与 Sandbox 服务**

三个 Python 服务使用固定 `YUXI_API_RUNTIME_IMAGE`，命令统一先执行：

```bash
/opt/source-deploy/check-runtime-lock.sh verify \
  /opt/runtime-locks/api.sha256 \
  /opt/source-locks/backend.uv.lock \
  /opt/source-locks/package.uv.lock
```

校验成功后 `exec` 原生产命令。Sandbox 使用固定 Runtime 镜像并只读挂载 `app.py` 和 `sandbox.env`。

- [ ] **Step 5: 实现 Builder、Web 和 HyCanvas 服务**

`release-builder` 使用统一 Builder 基础镜像，挂载源码、发布目录和构建缓存。Web 使用固定 Nginx 镜像并挂载 Nginx 配置与 `current/web-dist`。HyCanvas 使用固定 Runtime 镜像并挂载 `current/hycanvas` 和持久化存储。

Builder 不作为长期运行服务，配置 `profiles: [release]` 和 `restart: "no"`，由部署脚本通过 `docker compose --profile release run --rm release-builder` 显式执行。

- [ ] **Step 6: 迁移基础设施数据卷和模板**

把生产基础设施的相对 `./docker/volumes/...` 改为 `${DATA_ROOT}`。`.env.source.template` 包含所有路径、固定基础镜像引用、端口、Compose 项目名和现有生产必填密钥的安全占位说明，不提供公开默认生产密码。

- [ ] **Step 7: 运行契约和 Compose 解析测试**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_source_compose_contract.py -v
docker compose --env-file .env.source.test -f docker-compose.source.yml config --quiet
```

测试前从模板生成只存在于本机且被 `.gitignore` 忽略的 `.env.source.test`，填入测试专用强随机值和绝对临时目录。Expected: PASS；解析后的业务服务均无 `build:`。

- [ ] **Step 8: 提交**

```bash
git add docker-compose.source.yml .env.source.template .gitignore backend/test/unit/deployment/test_source_compose_contract.py
git commit -m "feat(deploy): 增加源码挂载 Compose"
```

## Task 5：部署、健康检查与回滚

**Files:**
- Create: `scripts/source-deploy/deploy-source.sh`
- Create: `scripts/source-deploy/rollback-source.sh`
- Create: `backend/test/unit/deployment/test_source_deploy_scripts.py`

**Interfaces:**
- Consumes: `SOURCE_DEPLOY_ENV_FILE`、`SOURCE_DEPLOY_COMPOSE_FILE`、`SOURCE_DEPLOY_BRANCH`、Task 4 Compose 服务和 Task 3 发布链接。
- Produces: `.deploy/source-deploy/current-state.env`、`.deploy/source-deploy/releases/<git-sha>.env`；命令 `deploy-source.sh [--validate-only] [--no-pull]` 和 `rollback-source.sh [git-sha]`。

- [ ] **Step 0: 建立部署命令测试夹具**

测试夹具使用真实临时 Git 仓库，使用假 Docker/Curl 记录外部副作用。部署脚本通过正式环境变量接收仓库、配置和发布目录，因此测试不需要加入专用后门：

```python
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).parents[4]
DEPLOY_SCRIPT = ROOT / "scripts/source-deploy/deploy-source.sh"


@dataclass(frozen=True)
class DeployWorkspace:
    repo: Path
    release_root: Path
    env_file: Path
    fake_bin: Path
    docker_log: Path
    env: dict[str, str]


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def arrange_deploy_workspace(tmp_path: Path) -> DeployWorkspace:
    repo = tmp_path / "repo"
    release_root = tmp_path / "releases"
    fake_bin = tmp_path / "bin"
    docker_log = tmp_path / "docker.log"
    env_file = tmp_path / ".env.prod"
    repo.mkdir()
    fake_bin.mkdir()
    env_file.write_text("COMPOSE_PROJECT_NAME=source-deploy-test\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("clean", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Source Deploy Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)

    write_executable(fake_bin / "docker", """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ -n "${FAKE_DOCKER_FAIL_PATTERN:-}" && "$*" == *"$FAKE_DOCKER_FAIL_PATTERN"* ]]; then exit 19; fi
exit 0
""")
    write_executable(fake_bin / "curl", """#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${FAKE_CURL_FAIL_FILE:-}" && -f "$FAKE_CURL_FAIL_FILE" ]]; then
  rm -f "$FAKE_CURL_FAIL_FILE"
  exit 22
fi
exit 0
""")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_DOCKER_LOG": str(docker_log),
            "SOURCE_DEPLOY_REPO": str(repo),
            "SOURCE_DEPLOY_RELEASE_ROOT": str(release_root),
            "SOURCE_DEPLOY_ENV_FILE": str(env_file),
            "SOURCE_DEPLOY_COMPOSE_FILE": str(ROOT / "docker-compose.source.yml"),
            "SOURCE_DEPLOY_BRANCH": "main",
            "HEALTH_ATTEMPTS": "1",
            "HEALTH_INTERVAL_SECONDS": "0",
        }
    )
    return DeployWorkspace(repo, release_root, env_file, fake_bin, docker_log, env)


def run_deploy(workspace: DeployWorkspace, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), *args],
        cwd=workspace.repo,
        env=workspace.env,
        text=True,
        capture_output=True,
        check=False,
    )
```

- [ ] **Step 1: 编写工作区保护和命令顺序测试**

使用临时 PATH 中的假 `docker` 与受控临时 Git 仓库记录命令：

```python
def test_deploy_rejects_dirty_tracked_worktree_before_stopping_services(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)
    (workspace.repo / "tracked.txt").write_text("dirty", encoding="utf-8")
    result = run_deploy(workspace)

    assert result.returncode != 0
    assert "未提交的已跟踪文件" in result.stderr
    assert " compose stop " not in workspace.docker_log.read_text(encoding="utf-8")


def test_deploy_builds_before_recreating_runtime_services(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)
    result = run_deploy(workspace, "--no-pull")
    calls = workspace.docker_log.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert calls.index("run --rm release-builder") < calls.index("up -d --force-recreate")
```

- [ ] **Step 2: 编写健康失败回滚测试**

假 Docker 在新版本健康检查时失败、恢复旧版本后成功。断言：

- Git 切回旧 SHA。
- `current` 恢复到旧 release。
- 旧业务服务被重新创建。
- 状态文件仍指向旧 SHA。
- 脚本返回非零，不能把回滚成功误报成发布成功。

- [ ] **Step 3: 运行测试并确认失败**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_source_deploy_scripts.py -v
```

Expected: FAIL，部署脚本尚不存在。

- [ ] **Step 4: 实现公共安全校验**

两个脚本都必须：

- 使用 `set -euo pipefail`。
- 解析并验证绝对目录，不接受 `/`、`$HOME`、空路径或未解析变量作为删除/移动目标。
- 使用 `git status --porcelain --untracked-files=no` 保护已有修改。
- 使用 `git pull --ff-only`，禁止隐式 merge。
- 通过同一 `compose()` 函数固定 env-file 和 Compose 文件。
- 在任何状态变更前运行 `docker compose config --quiet`。

- [ ] **Step 5: 实现部署主流程**

顺序固定为：

1. validate/preflight。
2. 记录旧 Git SHA、分支、current/previous release。
3. 停止四个直接读取源码的服务。
4. 未传 `--no-pull` 时拉取配置分支。
5. 运行 API lock verifier 容器。
6. 运行 release-builder。
7. 重建 Python、Sandbox、Web、HyCanvas 和 HyCanvas Init。
8. 轮询 API、Gateway、Worker、Web、HyCanvas 健康状态。
9. 原子写入部署状态文件。

`--validate-only` 只执行 Git、路径、环境、Compose 和基础镜像存在性校验，不停止、拉取、构建或重建任何服务。

- [ ] **Step 6: 实现失败回滚和显式回滚**

部署 trap 只在已经停止服务或切换 release 后触发回滚。回滚使用已记录的精确旧 SHA，通过 `git switch --detach <sha>` 恢复源码，并恢复 `current` 链接后重建旧服务。状态文件记录原分支；下一次正式部署必须显式切回配置分支再执行 `git pull --ff-only`。

`rollback-source.sh` 默认读取 `current-state.env` 的 previous SHA，也允许传入已存在且有 manifest 的历史 SHA；拒绝未知 SHA 和缺少产物的版本。

- [ ] **Step 7: 运行脚本测试和 Shell 静态检查**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment/test_source_deploy_scripts.py -v
docker run --rm -v "$PWD:/repo:ro" koalaman/shellcheck-alpine:stable \
  /repo/scripts/source-deploy/check-runtime-lock.sh \
  /repo/scripts/source-deploy/build-release.sh \
  /repo/scripts/source-deploy/deploy-source.sh \
  /repo/scripts/source-deploy/rollback-source.sh
```

Expected: PASS，ShellCheck 无 error。

- [ ] **Step 8: 提交**

```bash
git add scripts/source-deploy/deploy-source.sh scripts/source-deploy/rollback-source.sh backend/test/unit/deployment/test_source_deploy_scripts.py
git commit -m "feat(deploy): 增加源码发布与回滚流程"
```

## Task 6：运维文档、导航和变更记录

**Files:**
- Create: `docs/advanced/source-deployment.md`
- Modify: `docs/.vitepress/config.mts`
- Modify: `docs/develop-guides/changelog.md`

**Interfaces:**
- Consumes: Tasks 1-5 的实际命令、路径和失败行为。
- Produces: 运维可独立执行的首次部署、普通更新、依赖升级、日志、备份与回滚手册。

- [ ] **Step 1: 编写文档验收检查**

在部署单元测试中加入文档契约：文档必须包含以下精确主题标题或命令关键词：

```python
from pathlib import Path


ROOT = Path(__file__).parents[4]


def test_source_deployment_docs_cover_operational_workflows():
    content = (ROOT / "docs/advanced/source-deployment.md").read_text(encoding="utf-8")
    for required in (
        "首次部署",
        "日常更新",
        "基础镜像升级",
        "数据备份",
        "回滚",
        "--validate-only",
        "git pull --ff-only",
        "docker compose",
    ):
        assert required in content
```

- [ ] **Step 2: 运行检查并确认失败**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment -v
```

Expected: FAIL，正式文档不存在。

- [ ] **Step 3: 编写完整运维指南**

文档必须包括：

- 基础镜像一次性准备和版本变量。
- 服务器目录、权限和 `.env.prod` 准备。
- 首次 Git clone 和首次发布。
- 日常 `deploy-source.sh` 更新。
- 运维已自行 pull 时的 `--no-pull` 用法。
- lock 变化时基础镜像升级流程。
- Web/HyCanvas 构建缓存说明。
- API、Worker、Gateway、Builder、Web、HyCanvas 日志命令。
- PostgreSQL、MinIO、Neo4j、Milvus 和 HyCanvas 数据备份边界。
- 自动回滚和显式回滚。
- 常见错误：脏工作区、lock mismatch、Builder 失败、缺少 current、健康检查失败。

- [ ] **Step 4: 更新导航和 changelog**

把“源码挂载部署”加入 `docs/.vitepress/config.mts` 的高级部署分组；在 changelog 顶部现有版本段落追加一条完整说明，不创建重复版本标题。

- [ ] **Step 5: 运行文档与部署单元测试**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add docs/advanced/source-deployment.md docs/.vitepress/config.mts docs/develop-guides/changelog.md backend/test/unit/deployment
git commit -m "docs: 补充源码挂载部署指南"
```

## Task 7：全链路验证与验收证据

**Files:**
- Modify only if verification reveals a defect in files from Tasks 1-6.

**Interfaces:**
- Consumes: 完整源码部署实现和独立测试环境路径。
- Produces: 与设计规格第 13 节逐项对应的命令输出、镜像检查、容器状态和回滚证据。

- [ ] **Step 1: 检查工作区和 Compose 展开结果**

Run:

```bash
git status --short
docker compose --env-file .env.source.test -f docker-compose.source.yml config --quiet
docker compose --env-file .env.source.test -f docker-compose.source.yml config > .tmp/source-compose.rendered.yml
rg -n "build:|--reload|watchfiles|vite.*dev|air" .tmp/source-compose.rendered.yml
```

Expected: config 通过；业务服务不存在 `build:`、reload、watchfiles、Vite dev 或 Air。只允许非业务依赖中与字符串同名的无关内容，并逐项人工确认。

- [ ] **Step 2: 运行全部部署单元测试**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit/deployment -v
```

Expected: PASS，无 skip。

- [ ] **Step 3: 执行格式化和静态检查**

Run:

```bash
make format
docker run --rm -v "$PWD:/repo:ro" koalaman/shellcheck-alpine:stable /repo/scripts/source-deploy/*.sh
```

Expected: 返回 0；确认格式化没有修改任务范围外文件。

- [ ] **Step 4: 构建一次基础镜像并记录 ID**

Run Task 1/2 的五个基础镜像构建命令，记录：

```bash
docker image inspect \
  contentswarm-api-runtime:test \
  contentswarm-sandbox-runtime:test \
  contentswarm-web-builder:test \
  contentswarm-release-builder:test \
  contentswarm-hycanvas-runtime:test \
  --format '{{.RepoTags}} {{.Id}}' > .tmp/source-base-image-ids.before
```

- [ ] **Step 5: 启动隔离的源码部署测试栈**

使用独立 `COMPOSE_PROJECT_NAME=contentswarm-source-acceptance`、临时 `DATA_ROOT/RELEASE_ROOT/CACHE_ROOT` 和不冲突端口：

```bash
docker compose --env-file .env.source.test -f docker-compose.source.yml --profile release run --rm release-builder
docker compose --env-file .env.source.test -f docker-compose.source.yml up -d
docker compose --env-file .env.source.test -f docker-compose.source.yml ps
```

Expected: 所有默认服务进入 healthy/running，所有一次性 init 服务成功退出。

- [ ] **Step 6: 验证基础镜像不含业务代码**

Run:

```bash
docker run --rm contentswarm-api-runtime:test sh -lc 'test ! -e /app/server && test ! -e /app/package/yuxi'
docker run --rm contentswarm-sandbox-runtime:test sh -lc 'test ! -e /app/app.py'
docker run --rm nginx:1.31.2-alpine sh -lc 'test ! -e /usr/share/nginx/html/assets'
docker run --rm contentswarm-hycanvas-runtime:test sh -lc 'test ! -e /app/hycanvas'
```

Expected: 全部返回 0。

- [ ] **Step 7: 验证 Python、Web 和 HyCanvas 三类更新**

在专用验收分支分别提交最小可观察版本标记：

1. Python 健康接口测试字段。
2. Web 构建产物中的验收标记。
3. HyCanvas 版本字符串。

每次使用 `deploy-source.sh --no-pull` 发布并通过真实 HTTP/容器命令确认标记变化。随后撤销仅用于验收的标记提交，不把测试标记合入最终分支。整个过程中再次记录基础镜像 ID。

Expected: 三类功能均更新，`.tmp/source-base-image-ids.before` 与 after 完全一致。

- [ ] **Step 8: 验证依赖失配和失败回滚**

在专用验收分支依次：

1. 修改 lock 文件但不更新基础镜像，确认部署在启动前失败。
2. 制造 HyCanvas 编译失败，确认 current 未切换。
3. 制造新 API 健康检查失败，确认 Git SHA 和 current 均恢复。

每次验证后恢复验收分支，确认旧版本健康且持久化数据目录未变化。

- [ ] **Step 9: 运行项目回归测试**

Run:

```bash
docker compose exec -T api uv run --group test pytest test/unit -m "not slow"
docker compose exec -T api uv run --group test pytest test/integration
docker compose exec -T api uv run --group test pytest test/e2e -m e2e
make format
```

Expected: 全部通过。外部可选服务导致的 skip 只允许符合 `testing-guidelines.md` 的两类情况，并在验收记录中逐项说明。

- [ ] **Step 10: 清理隔离测试栈和临时目录**

只针对明确的验收项目执行：

```bash
docker compose --env-file .env.source.test -f docker-compose.source.yml down
```

验证临时路径处于工作区 `.tmp/source-deployment-acceptance` 后再删除该目录；不得删除真实 `${DATA_ROOT}` 或现有开发环境卷。

- [ ] **Step 11: 最终提交**

如果验证未产生代码修复则不创建空提交；如产生修复：

```bash
git status --short
git add -- docker-compose.source.yml docker/runtime scripts/source-deploy backend/test/unit/deployment docs/advanced/source-deployment.md docs/.vitepress/config.mts docs/develop-guides/changelog.md .env.source.template .gitignore
git commit -m "fix(deploy): 完善源码部署验收问题"
```

## 完成判定

只有在以下证据同时成立后才能宣布完成：

- Tasks 1-6 的提交均存在且工作区无意外改动。
- `backend/test/unit/deployment` 全部通过。
- Compose 配置在隔离测试环境可展开并启动。
- 五类基础镜像完成无业务代码检查。
- Python、Web、HyCanvas 更新均不改变基础镜像 ID。
- lock mismatch、Builder 失败和健康失败三种负向场景均按规格阻断或回滚。
- 完整单元、集成、E2E、Lint/Format 验证通过，或外部环境限制被明确记录且不被误报为完成。
- 运维文档中的命令与实际实现一致。
