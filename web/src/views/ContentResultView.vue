<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileClock,
  FilePenLine,
  ImagePlus,
  Link2,
  LoaderCircle,
  Send,
  ShieldCheck,
  UserRoundCog,
  Workflow
} from 'lucide-vue-next'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import XiaohongshuDistributionDrawer from '@/components/content/XiaohongshuDistributionDrawer.vue'
import { contentApi } from '@/apis/content_api'
import { buildFormulaPresentation } from '@/utils/contentWorkflowPresentation'
import { formatDateTime } from '@/utils/time'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const task = ref(null)
const template = ref(null)
const artifact = ref(null)
const versions = ref([])
const runAudit = ref(null)
const distributionOpen = ref(false)
const coverUrl = ref('')

const taskId = computed(() => route.params.taskId)
const review = computed(() => artifact.value?.review_snapshot || task.value?.review || {})
const reviewChecks = computed(() => review.value.checks || [])
const evidenceItems = computed(() => artifact.value?.evidence_snapshot?.items || [])
const strategy = computed(() => artifact.value?.strategy_snapshot || task.value?.strategy || {})
const reviewStatus = computed(() => review.value.status || 'pending')
const matchDecision = computed(() => runAudit.value?.match_decision || {})
const titleFormula = computed(() => buildFormulaPresentation(strategy.value, 'title'))
const bodyFormula = computed(() => buildFormulaPresentation(strategy.value, 'body'))
const delegatedAgents = computed(() => runAudit.value?.delegated_agents || [])
const auditSummary = computed(() => runAudit.value?.event_summary || {})
const skillEvents = computed(() =>
  (runAudit.value?.events || []).filter((item) => item.event_type === 'content.skill.activated')
)
const canDistribute = computed(
  () => artifact.value && ['passed', 'warning'].includes(reviewStatus.value)
)

const taskStatusLabels = {
  review_required: '待审核',
  reviewed: '已审核',
  review_blocked: '审核阻断',
  completed: '已完成'
}
const reviewStatusLabels = {
  pending: '待审核',
  passed: '审核通过',
  warning: '有风险提示',
  blocked: '审核阻断'
}
const sourceTypeLabels = {
  manual_input: '人工输入',
  knowledge_base: '知识库',
  business_api: '业务系统',
  mcp: '业务工具'
}
const verifiedStatusLabels = {
  user_confirmed: '人工确认',
  retrieved: '知识检索',
  needs_confirmation: '待确认'
}

const formatEvidenceValue = (value) => {
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value ?? '-')
}

const loadResult = async () => {
  if (!taskId.value) return
  loading.value = true
  try {
    const response = await contentApi.getTask(taskId.value)
    task.value = response.task
    template.value = response.template
    artifact.value = response.artifact
    if (coverUrl.value) {
      URL.revokeObjectURL(coverUrl.value)
      coverUrl.value = ''
    }
    if (artifact.value?.id) {
      const versionResponse = await contentApi.listArtifactVersions(artifact.value.id)
      versions.value = versionResponse.items || []
    } else {
      versions.value = []
    }
    runAudit.value = task.value?.latest_run_id
      ? await contentApi.getRun(task.value.latest_run_id)
      : null
    if (artifact.value?.cover_asset_id) {
      try {
        const coverResponse = await contentApi.getCoverAssetFile(artifact.value.cover_asset_id)
        coverUrl.value = URL.createObjectURL(await coverResponse.blob())
      } catch (error) {
        message.warning(error.message || '当前封面暂时无法预览')
      }
    }
  } catch (error) {
    message.error(error.message || '内容结果加载失败')
  } finally {
    loading.value = false
  }
}

watch(taskId, loadResult, { immediate: true })
onBeforeUnmount(() => {
  if (coverUrl.value) URL.revokeObjectURL(coverUrl.value)
})
</script>

<template>
  <div class="content-result-page">
    <div v-if="loading" class="page-state">
      <LoaderCircle :size="28" class="spin" />
      <span>正在加载内容详情</span>
    </div>

    <template v-else-if="task">
      <header class="result-header">
        <button type="button" class="back-button" @click="router.push('/content/new')">
          <ArrowLeft :size="17" />
          <span>内容生产</span>
        </button>
        <div class="header-actions">
          <span class="task-status" :class="task.status">
            {{ taskStatusLabels[task.status] || task.status }}
          </span>
          <a-button @click="router.push('/content/accounts')">
            <UserRoundCog :size="16" />账号管理
          </a-button>
          <a-button @click="router.push(`/content/tasks/${task.id}`)">
            <FilePenLine :size="16" />继续编辑
          </a-button>
          <a-tooltip v-if="!canDistribute" title="内容审核未通过，修订并重新审核后才能分发">
            <a-button type="primary" disabled><Send :size="16" />分发到小红书</a-button>
          </a-tooltip>
          <a-button v-else type="primary" @click="distributionOpen = true">
            <Send :size="16" />分发到小红书
          </a-button>
        </div>
      </header>

      <main v-if="artifact" class="result-layout">
        <article class="content-card">
          <div v-if="coverUrl" class="selected-cover">
            <img :src="coverUrl" alt="当前内容封面" />
            <div><strong>当前分发封面</strong><span>小红书分发将优先使用这张封面</span></div>
            <button @click="router.push({ path: '/content/covers', query: { task: taskId } })">重新生成</button>
          </div>
          <div class="content-meta">
            <span>{{ template?.name || '通用内容' }}</span>
            <span>v{{ artifact.current_version }}</span>
            <span>{{ formatDateTime(artifact.updated_at) }}</span>
          </div>
          <h1>{{ artifact.title }}</h1>
          <div v-if="artifact.topics?.length" class="topic-list">
            <span v-for="topic in artifact.topics" :key="topic">#{{ topic }}</span>
          </div>
          <div class="content-body">
            <MarkdownPreview :content="artifact.body" />
          </div>
          <button class="cover-link" @click="router.push({ path: '/content/covers', query: { task: taskId } })">
            <ImagePlus :size="15" />{{ artifact.cover_asset_id ? '管理封面' : '生成专属封面' }}
          </button>
        </article>

        <aside class="detail-sidebar">
          <section class="detail-card">
            <div class="card-heading">
              <ShieldCheck :size="18" />
              <h2>质量审核</h2>
              <span class="review-status" :class="review.status || 'pending'">
                {{ reviewStatusLabels[review.status] || '待审核' }}
              </span>
            </div>
            <div v-if="reviewChecks.length" class="review-list">
              <div
                v-for="check in reviewChecks"
                :key="`${check.code}-${check.location}-${check.message}`"
                class="review-item"
                :class="check.level"
              >
                <CircleAlert :size="15" />
                <div>
                  <strong>{{ check.message }}</strong>
                  <p v-if="check.suggestion">{{ check.suggestion }}</p>
                </div>
              </div>
            </div>
            <div v-else class="empty-detail"><CheckCircle2 :size="17" />暂无审核问题</div>
          </section>

          <section class="detail-card">
            <div class="card-heading">
              <Workflow :size="18" />
              <h2>V3 运行审计</h2>
              <span>{{ delegatedAgents.length }} 个 Agent 子运行</span>
            </div>
            <dl class="trace-grid audit-grid">
              <dt>命中组合组</dt>
              <dd>{{ matchDecision.selected_group_id || '-' }}</dd>
              <dt>标题公式</dt>
              <dd class="formula-detail">
                <strong>{{ titleFormula.name }}</strong>
                <span v-if="titleFormula.detail">{{ titleFormula.detail }}</span>
              </dd>
              <dt>正文公式</dt>
              <dd class="formula-detail">
                <strong>{{ bodyFormula.name }}</strong>
                <span v-if="bodyFormula.detail">{{ bodyFormula.detail }}</span>
              </dd>
              <dt>Skill 激活</dt>
              <dd>{{ auditSummary.skill_activation_count ?? 0 }} 次</dd>
              <dt>工具事件</dt>
              <dd>{{ auditSummary.tool_event_count ?? 0 }} 条</dd>
              <dt>知识库检索</dt>
              <dd>{{ auditSummary.knowledge_retrieval_count ?? 0 }} 次 / {{ auditSummary.knowledge_result_count ?? 0 }} 条引用</dd>
            </dl>
            <div v-if="delegatedAgents.length" class="agent-trace-list">
              <div v-for="item in delegatedAgents" :key="item.run_id" class="agent-trace-item">
                <Bot :size="15" />
                <div>
                  <strong>{{ item.agent_slug }} · {{ item.node_id }}</strong>
                  <span>{{ item.status }} · {{ item.runtime_config_snapshot?.model || '默认模型' }}</span>
                </div>
              </div>
            </div>
            <div v-else class="empty-detail">本次运行没有 Agent 子运行记录</div>
            <div v-if="skillEvents.length" class="skill-trace-list">
              <span v-for="(item, index) in skillEvents" :key="`${item.run_id}-${item.payload.skill_slug}-${index}`">
                {{ item.payload.skill_slug }}@{{ item.payload.skill_version }}
              </span>
            </div>
          </section>

          <section class="detail-card">
            <div class="card-heading">
              <Link2 :size="18" />
              <h2>来源追溯</h2>
              <span>{{ evidenceItems.length }} 条</span>
            </div>
            <div v-if="evidenceItems.length" class="evidence-list">
              <div v-for="item in evidenceItems" :key="item.id" class="evidence-item">
                <strong>{{ item.key || '内容依据' }}</strong>
                <p>{{ formatEvidenceValue(item.value) }}</p>
                <span>
                  {{ sourceTypeLabels[item.source_type] || item.source_type }} ·
                  {{ verifiedStatusLabels[item.verified_status] || item.verified_status }}
                </span>
              </div>
            </div>
            <div v-else class="empty-detail">暂无来源记录</div>
          </section>

          <section class="detail-card">
            <div class="card-heading">
              <FileClock :size="18" />
              <h2>生成记录</h2>
            </div>
            <dl class="trace-grid">
              <dt>创作手法</dt>
              <dd>{{ strategy.methods?.join('、') || '-' }}</dd>
              <dt>标题公式</dt>
              <dd class="formula-detail">
                <strong>{{ titleFormula.name }}</strong>
                <span v-if="titleFormula.detail">{{ titleFormula.detail }}</span>
              </dd>
              <dt>正文公式</dt>
              <dd class="formula-detail">
                <strong>{{ bodyFormula.name }}</strong>
                <span v-if="bodyFormula.detail">{{ bodyFormula.detail }}</span>
              </dd>
              <dt>规则版本</dt>
              <dd>{{ task.rule_version_id || '-' }}</dd>
            </dl>
            <div class="version-list">
              <div v-for="item in versions" :key="item.id" class="version-item">
                <Clock3 :size="14" />
                <div>
                  <strong
                    >v{{ item.version }} ·
                    {{ item.source_type === 'generated' ? 'AI 生成' : '人工编辑' }}</strong
                  >
                  <span
                    >{{ formatDateTime(item.created_at) }} ·
                    {{ item.model_spec || '未指定模型' }}</span
                  >
                </div>
              </div>
            </div>
          </section>
        </aside>
      </main>

      <div v-else class="page-state empty-result">
        <FileClock :size="28" />
        <h2>内容结果尚未生成</h2>
        <p>返回工作台继续执行生成流程，完成后即可在这里查看。</p>
        <a-button type="primary" @click="router.push(`/content/tasks/${task.id}`)"
          >返回任务</a-button
        >
      </div>
    </template>

    <div v-else class="page-state">
      <h2>未找到内容结果</h2>
      <a-button @click="router.push('/content/new')">返回内容生产</a-button>
    </div>

    <XiaohongshuDistributionDrawer v-model:open="distributionOpen" :artifact="artifact" />
  </div>
</template>

<style scoped lang="less">
.content-result-page {
  min-width: 0;
  min-height: 100vh;
  padding: 22px var(--page-padding) 48px;
  background: var(--gray-25);
  color: var(--color-text);
}

.result-header,
.result-layout {
  max-width: 1240px;
  margin-right: auto;
  margin-left: auto;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.back-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 4px;
  border: 0;
  background: transparent;
  color: var(--gray-700);
  cursor: pointer;
  font-size: 14px;

  &:hover {
    color: var(--main-color);
  }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.task-status,
.review-status {
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
}

.task-status.completed,
.task-status.reviewed,
.review-status.passed {
  background: var(--color-success-50);
  color: var(--color-success-700);
}

.task-status.review_blocked,
.review-status.blocked {
  background: var(--color-error-50);
  color: var(--color-error-700);
}

.review-status.warning {
  background: var(--color-warning-50);
  color: var(--color-warning-900);
}

.result-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(310px, 0.75fr);
  gap: 18px;
  align-items: start;
}

.content-card,
.detail-card {
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
}

.content-card {
  min-height: calc(100vh - 110px);
  padding: 44px 52px 64px;

  h1 {
    max-width: 760px;
    margin: 12px 0 14px;
    color: var(--gray-1000);
    font-size: clamp(26px, 3vw, 36px);
    line-height: 1.35;
    letter-spacing: -0.02em;
  }
}

.selected-cover {
  display: grid;
  grid-template-columns: 90px 1fr auto;
  gap: 13px;
  align-items: center;
  margin-bottom: 24px;
  padding: 11px;
  border: 1px solid var(--main-100);
  border-radius: 10px;
  background: var(--main-30);

  img { width: 90px; height: 112px; border-radius: 7px; object-fit: cover; }
  div { display: grid; gap: 4px; }
  span { color: var(--color-text-secondary); font-size: 12px; }
  button { border: 0; background: transparent; color: var(--main-700); cursor: pointer; }
}

.cover-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 20px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-700);
  cursor: pointer;
}

.content-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.topic-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 28px;

  span {
    padding: 4px 9px;
    border-radius: 999px;
    background: var(--main-30);
    color: var(--main-700);
    font-size: 12px;
  }
}

.content-body {
  padding-top: 26px;
  border-top: 1px solid var(--gray-150);

  :deep(.yk-markdown-preview) {
    font-size: 16px;
    line-height: 1.9;
  }
}

.detail-sidebar {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-card {
  padding: 18px;
}

.card-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;

  h2 {
    flex: 1;
    margin: 0;
    font-size: 15px;
  }

  > span:not(.review-status) {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}

.review-list,
.evidence-list,
.version-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.review-item {
  display: flex;
  gap: 8px;
  padding: 10px;
  border-radius: 7px;
  background: var(--color-info-50);
  color: var(--color-info-700);

  &.warning {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }

  &.error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }

  svg {
    flex: 0 0 auto;
    margin-top: 2px;
  }

  strong {
    display: block;
    font-size: 12px;
  }

  p {
    margin: 4px 0 0;
    color: currentColor;
    font-size: 12px;
    opacity: 0.82;
  }
}

.evidence-item {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--gray-100);

  &:last-child {
    padding-bottom: 0;
    border-bottom: 0;
  }

  strong,
  p,
  span {
    display: block;
  }

  strong {
    font-size: 12px;
  }

  p {
    display: -webkit-box;
    margin: 5px 0;
    overflow: hidden;
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.6;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
  }

  span {
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
}

.empty-detail {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.trace-grid {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 9px;
  margin: 0 0 16px;
  font-size: 12px;

  dt {
    color: var(--color-text-tertiary);
  }

  dd {
    margin: 0;
    overflow-wrap: anywhere;
    color: var(--gray-800);
  }
}

.version-list {
  padding-top: 14px;
  border-top: 1px solid var(--gray-100);
}

.audit-grid {
  grid-template-columns: 86px minmax(0, 1fr);
}

.formula-detail {
  display: flex;
  flex-direction: column;
  gap: 3px;

  strong { color: var(--color-text); font-size: 12px; }
  span { color: var(--color-text-secondary); line-height: 1.55; }
}

.agent-trace-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-100);
}

.agent-trace-item {
  display: flex;
  gap: 8px;
  color: var(--main-700);

  svg { flex: 0 0 auto; margin-top: 2px; }
  strong, span { display: block; font-size: 11px; }
  span { margin-top: 2px; color: var(--color-text-tertiary); }
}

.skill-trace-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 12px;

  span {
    padding: 3px 7px;
    border-radius: 999px;
    background: var(--main-30);
    color: var(--main-700);
    font-size: 10px;
  }
}

.version-item {
  display: flex;
  gap: 8px;
  color: var(--gray-600);

  svg {
    flex: 0 0 auto;
    margin-top: 2px;
  }

  strong,
  span {
    display: block;
    font-size: 11px;
  }

  strong {
    margin-bottom: 2px;
    color: var(--gray-800);
  }
}

.page-state {
  display: flex;
  min-height: 60vh;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  color: var(--color-text-secondary);

  h2,
  p {
    margin: 0;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 980px) {
  .result-layout {
    grid-template-columns: 1fr;
  }

  .content-card {
    min-height: auto;
  }
}

@media (max-width: 640px) {
  .content-card {
    padding: 28px 20px 40px;
  }

  .result-header {
    align-items: flex-start;
  }

  .header-actions {
    align-items: flex-end;
    flex-direction: column;
  }
}
</style>
