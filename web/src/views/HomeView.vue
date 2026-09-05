<template>
  <div class="home-page">
    <div v-if="isLoading" class="page-state">
      <span class="loading-indicator" aria-hidden="true"></span>
      <p>正在连接内容生产平台...</p>
    </div>

    <div v-else-if="error" class="page-state page-error" role="alert">
      <h1>页面暂时无法加载</h1>
      <p>{{ error }}</p>
      <button class="primary-cta" type="button" @click="loadData">重试</button>
    </div>

    <template v-else>
      <header class="site-header">
        <button class="brand" type="button" aria-label="ContentFlow 首页" @click="router.push('/')">
          <img src="/contentflow-homepage-logo.svg" alt="ContentFlow" />
          <span>ContentFlow 内容策略 Agent</span>
        </button>

        <nav class="site-nav" aria-label="主要导航">
          <button v-for="item in navItems" :key="item.label" type="button" @click="goTo(item.path)">
            {{ item.label }}
          </button>
        </nav>

        <div class="header-actions">
          <button
            v-if="!userStore.isLoggedIn"
            class="login-link"
            type="button"
            @click="router.push('/login')"
          >
            登录
          </button>
          <UserInfoComponent v-else />
          <button class="header-cta" type="button" @click="goTo('/content/new')">
            免费使用
            <ArrowUpRight :size="16" />
          </button>
        </div>
      </header>

      <main>
        <section class="hero">
          <div class="hero-copy">
            <div class="eyebrow">
              <Sparkles :size="15" />
              ContentFlow · 行业增长获客系统
            </div>
            <h1>
              让业绩增长率提升60%
              <span>让获客成本低于行业30%</span>
            </h1>
            <p class="hero-description">
              ContentFlow 内容策略 Agent 将行业客群洞察、增长指标与真实服务价值串成可执行链路，
              持续获取更匹配的行业线索。
            </p>
            <div class="hero-actions">
              <button class="primary-cta" type="button" @click="goTo('/content/new')">
                开始增长获客
                <ArrowRight :size="18" />
              </button>
              <button class="secondary-cta" type="button" @click="goTo('/content/history')">
                <History :size="18" />
                了解增长链路
              </button>
            </div>
            <ul class="hero-trust" aria-label="平台价值">
              <li><strong>平均1.2W+</strong><span>AI平台获客量</span></li>
              <li><strong>100%</strong><span>基于真实业务场景</span></li>
              <li><strong>70%</strong><span>获客转化率</span></li>
            </ul>
          </div>

          <div class="studio-preview" aria-label="自动轮播的获客数据可视化看板">
            <div class="preview-header">
              <div>
                <span class="preview-kicker">VERIFIED GROWTH</span>
                <h2>获客增长看板</h2>
              </div>
              <div class="preview-status">
                <span class="running-status"><BadgeCheck :size="13" /> 数据已核验</span>
              </div>
            </div>

            <div class="dashboard-motion">
              <div class="dashboard-track">
                <section
                  v-for="panel in dashboardPanels"
                  :key="panel.id"
                  class="trend-panel dashboard-body"
                  aria-label="增长与成本趋势"
                >
                  <div class="trend-panel-head">
                    <div>
                      <span>近 6 个周期 · 周期 1 = 100</span>
                      <strong>
                        {{ panel.isAcquisition ? '新增获客与留资效率指数' : '增长与投入效率指数' }}
                      </strong>
                    </div>
                    <span class="period-badge">已完成周期</span>
                  </div>

                  <div
                    class="trend-chart"
                    :aria-label="
                      panel.isAcquisition
                        ? '新增顾客、新增客资和留资率的趋势图'
                        : '有效线索上升、单线索成本下降的趋势图'
                    "
                  >
                    <div class="chart-scale"><span>150</span><span>100</span><span>50</span></div>
                    <svg
                      viewBox="0 0 470 158"
                      preserveAspectRatio="none"
                      role="img"
                      :aria-label="
                        panel.isAcquisition
                          ? '新增顾客、新增客资和留资率持续提升的趋势图'
                          : '有效线索上升、单线索成本下降、开口率持续提升的趋势图'
                      "
                    >
                      <defs>
                        <linearGradient :id="'leadFill-' + panel.id" x1="0" x2="0" y1="0" y2="1">
                          <stop offset="0%" stop-color="#c52a39" stop-opacity=".22" />
                          <stop offset="100%" stop-color="#c52a39" stop-opacity="0" />
                        </linearGradient>
                      </defs>
                      <path class="chart-grid" d="M12 26H454M12 79H454M12 132H454" />
                      <path
                        class="lead-area"
                        :style="{ fill: 'url(#leadFill-' + panel.id + ')' }"
                        :d="
                          panel.isAcquisition
                            ? 'M18 123 C70 115 91 107 128 109 S184 85 220 88 S278 63 322 66 S384 41 450 46 L450 145 L18 145Z'
                            : 'M18 122 C75 112 89 101 128 105 S185 80 220 84 S280 48 322 58 S386 24 450 31 L450 145 L18 145Z'
                        "
                      />
                      <path
                        class="lead-line"
                        :d="
                          panel.isAcquisition
                            ? 'M18 123 C70 115 91 107 128 109 S184 85 220 88 S278 63 322 66 S384 41 450 46'
                            : 'M18 122 C75 112 89 101 128 105 S185 80 220 84 S280 48 322 58 S386 24 450 31'
                        "
                      />
                      <path
                        class="cost-line"
                        :d="
                          panel.isAcquisition
                            ? 'M18 129 C66 120 94 125 128 104 S183 103 220 82 S277 86 322 62 S384 60 450 37'
                            : 'M18 39 C68 48 91 55 128 50 S184 78 220 74 S276 104 322 97 S384 121 450 116'
                        "
                      />
                      <path
                        class="open-line"
                        :d="
                          panel.isAcquisition
                            ? 'M18 112 C66 106 94 111 128 96 S183 101 220 86 S278 89 322 70 S384 73 450 56'
                            : 'M18 108 C66 102 93 110 128 94 S183 99 220 85 S278 80 322 60 S385 63 450 38'
                        "
                      />
                      <circle class="lead-dot" cx="450" :cy="panel.isAcquisition ? 46 : 31" r="5" />
                      <circle
                        class="cost-dot"
                        cx="450"
                        :cy="panel.isAcquisition ? 37 : 116"
                        r="5"
                      />
                      <circle class="open-dot" cx="450" :cy="panel.isAcquisition ? 56 : 38" r="5" />
                      <text class="lead-value" x="416" :y="panel.isAcquisition ? 66 : 19">
                        {{ panel.isAcquisition ? '+64%' : '+66%' }}
                      </text>
                      <text class="cost-value" x="412" :y="panel.isAcquisition ? 26 : 138">
                        {{ panel.isAcquisition ? '+66%' : '-31%' }}
                      </text>
                      <text class="open-value" x="400" :y="panel.isAcquisition ? 78 : 59">
                        {{ panel.isAcquisition ? '42.8%' : '78.6%' }}
                      </text>
                    </svg>
                    <div class="chart-labels">
                      <span>周期 1</span><span>周期 3</span><span>周期 6</span>
                    </div>
                  </div>

                  <div class="trend-legend">
                    <template v-if="panel.isAcquisition">
                      <span class="lead-key"
                        ><TrendingUp :size="14" /> 新增顾客：783 → 1,286（+64%）</span
                      >
                      <span class="cost-key"
                        ><TrendingUp :size="14" /> 新增客资：10,762 → 17,865（+66%）</span
                      >
                      <span class="open-key"
                        ><MessageCircle :size="14" /> 留资率：31.6% → 42.8%（+11.2pt）</span
                      >
                    </template>
                    <template v-else>
                      <span class="lead-key"
                        ><TrendingUp :size="14" /> 有效线索：10,762 → 17,865（+66%）</span
                      >
                      <span class="cost-key"
                        ><TrendingDown :size="14" /> 单线索成本：¥125 → ¥86（-31%）</span
                      >
                      <span class="open-key"
                        ><MessageCircle :size="14" /> 开口率：65.8% → 78.6%（+12.8pt）</span
                      >
                    </template>
                  </div>
                </section>
              </div>
            </div>

            <div class="metric-grid">
              <article v-for="metric in activeMetrics" :key="metric.label" class="metric-card">
                <span class="metric-icon" :class="metric.tone">
                  <component :is="metric.icon" :size="17" />
                </span>
                <div>
                  <small>{{ metric.label }}</small>
                  <strong>{{ metric.value }}</strong>
                  <em :class="{ negative: metric.negative }">{{ metric.trend }}</em>
                </div>
              </article>
            </div>

            <div class="evidence-card verified-evidence">
              <div class="evidence-icon"><BadgeCheck :size="19" /></div>
              <div>
                <strong>结果来自已验证的业务数据</strong>
                <span>近 6 个周期 · 最新同步于 10:30</span>
                <div class="data-sources">
                  <span>内容表现 86 条</span>
                  <span>渠道投放 24 组</span>
                  <span>客户反馈 128 项</span>
                </div>
              </div>
              <span class="verified-status"><ShieldCheck :size="15" /> 可信</span>
            </div>
          </div>
        </section>

        <section class="value-section">
          <div class="section-intro value-intro">
            <div>
              <span>BUILT FOR REAL BUSINESS</span>
              <h2>不只生产内容，更管理行业获客效率</h2>
            </div>
            <p>从目标客群、成本投入到有效线索，建立一套可复用、可复盘的增长内容机制。</p>
          </div>

          <div class="value-grid">
            <article v-for="item in valueCards" :key="item.title" class="value-card">
              <div class="value-card-head">
                <span><component :is="item.icon" :size="18" /></span>
                <small>{{ item.index }}</small>
              </div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.desc }}</p>
              <span class="value-card-link">{{ item.action }} <ArrowRight :size="14" /></span>
            </article>
          </div>
        </section>

        <section class="process-section">
          <div class="section-intro centered-intro">
            <span>GROWTH WORKFLOW</span>
            <h2>一条从内容到有效线索的增长链路</h2>
            <p>把增长率、获客成本与行业需求，转化为可执行、可复盘的获客动作。</p>
          </div>

          <div class="process-grid">
            <article v-for="item in processSteps" :key="item.number" class="process-card">
              <span class="process-number">{{ item.number }}</span>
              <h3>{{ item.title }}</h3>
              <div>
                <small>你来完成</small>
                <p>{{ item.action }}</p>
              </div>
              <div class="process-result">
                <small>平台交付</small>
                <strong>{{ item.result }}</strong>
              </div>
            </article>
          </div>
        </section>

        <section class="asset-section">
          <div class="asset-layout">
            <div class="asset-copy">
              <span>FOR HOME IMPROVEMENT GROWTH</span>
              <h2>让每个行业增长场景，都能找到更有效的客户</h2>
              <p>
                无论是品牌推广、产品营销、客户运营还是渠道活动，都能围绕真实需求减少无效触达，沉淀可复用的增长资产。
              </p>
              <button class="asset-cta" type="button" @click="goTo('/content/new')">
                配置增长策略
                <ArrowRight :size="17" />
              </button>
            </div>

            <div class="asset-grid">
              <article v-for="item in scenarios" :key="item.title" class="asset-card">
                <span><component :is="item.icon" :size="18" /></span>
                <h3>{{ item.title }}</h3>
                <p>{{ item.desc }}</p>
              </article>
            </div>
          </div>

          <div class="final-cta">
            <div>
              <span>ContentFlow 内容策略 Agent</span>
              <h2>从一个增长目标开始，获取下一条有效行业线索。</h2>
            </div>
            <button type="button" @click="goTo('/content/new')">
              进入增长工作台
              <ArrowRight :size="18" />
            </button>
          </div>
        </section>
      </main>

      <footer class="site-footer">
        <div class="footer-brand">
          <img src="/contentflow-homepage-mark.svg" alt="" />
          ContentFlow · 内容增长系统
        </div>
        <p>© {{ currentYear }} ContentFlow. 让行业知识与真实业务，成为持续增长的内容资产。</p>
      </footer>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useInfoStore } from '@/stores/info'
import { healthApi } from '@/apis/system_api'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import {
  ArrowRight,
  ArrowUpRight,
  BadgeCheck,
  ClipboardCheck,
  FileClock,
  FilePenLine,
  History,
  Images,
  LibraryBig,
  MessageCircle,
  ShieldCheck,
  Sparkles,
  TrendingDown,
  TrendingUp,
  UserRoundPlus
} from 'lucide-vue-next'

const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const isLoading = ref(true)
const error = ref('')
const showAcquisitionMetrics = ref(false)
const currentYear = new Date().getFullYear()
let dashboardTimer

const navItems = [
  { label: '平台能力', path: '/content/new' },
  { label: '内容流程', path: '/content/admin/rules' },
  { label: '适用场景', path: '/knowledge' }
]

const dashboardPanels = [
  { id: 'growth-a', isAcquisition: false },
  { id: 'acquisition-a', isAcquisition: true },
  { id: 'growth-b', isAcquisition: false },
  { id: 'acquisition-b', isAcquisition: true }
]

const metricSets = {
  growth: [
    {
      label: '有效线索',
      value: '17,865',
      trend: '较上期 +18.6%',
      tone: 'lead',
      icon: TrendingUp
    },
    {
      label: '获客成本',
      value: '¥86',
      trend: '较上期 -12.4%',
      tone: 'cost',
      icon: TrendingDown
    },
    {
      label: '开口率',
      value: '78.6%',
      trend: '较上期 +5.4pt',
      tone: 'open',
      icon: MessageCircle
    },
    {
      label: '数据可信度',
      value: '98.4%',
      trend: '已核验 128 项',
      tone: 'trust',
      icon: ShieldCheck
    }
  ],
  acquisition: [
    {
      label: '新增顾客数',
      value: '5793',
      trend: '较上期 +6.18%',
      tone: 'lead',
      icon: TrendingUp
    },
    {
      label: '新增客资数',
      value: '2857',
      trend: '较上期 -1.38%',
      tone: 'cost',
      icon: TrendingDown,
      negative: true
    },
    {
      label: '留资率',
      value: '67.89%',
      trend: '较上期 -5.27%',
      tone: 'open',
      icon: MessageCircle,
      negative: true
    },
    {
      label: '开口人数',
      value: '4208',
      trend: '较上期 +4.11%',
      tone: 'trust',
      icon: UserRoundPlus
    }
  ]
}

const activeMetrics = computed(() =>
  showAcquisitionMetrics.value ? metricSets.acquisition : metricSets.growth
)

const valueCards = [
  {
    index: '01',
    title: '增长率目标对齐',
    desc: '将区域、客群、内容表现与增长目标同步输入，让每条内容都有清晰的业务方向。',
    icon: FilePenLine,
    action: '增长目标'
  },
  {
    index: '02',
    title: '获客成本优化',
    desc: '以行业痛点、服务价值和成本变化重组表达，把预算优先投向更可能咨询的人群。',
    icon: TrendingDown,
    action: '成本策略'
  },
  {
    index: '03',
    title: '有效线索获取',
    desc: '用场景诊断、案例佐证与预约引导筛出真实行业需求，提升后续到店与成交效率。',
    icon: ClipboardCheck,
    action: '线索转化'
  }
]

const processSteps = [
  {
    number: '01',
    title: '设定增长目标',
    action: '录入区域、目标客群、预期增长率与单线索成本，建立获客简报。',
    result: '建立增长目标'
  },
  {
    number: '02',
    title: '匹配获客策略',
    action: '选择增长、成本或线索策略，明确内容要回应的行业需求。',
    result: '明确获客策略'
  },
  {
    number: '03',
    title: '生成留资内容',
    action: '把真实数据、服务价值和行动入口组合为可编辑的内容预览。',
    result: '获得留资内容'
  },
  {
    number: '04',
    title: '复盘线索质量',
    action: '回看咨询、到店与成本表现，让下一轮投放更聚焦有效获客。',
    result: '聚焦有效获客'
  }
]

const scenarios = [
  {
    title: '品牌新品推广',
    desc: '聚焦产品价值与目标人群，提升咨询、试用与转化质量。',
    icon: History
  },
  {
    title: '客户需求激活',
    desc: '用场景洞察与解决方案筛选真正有需求的潜在人群。',
    icon: FileClock
  },
  {
    title: '渠道成本优化',
    desc: '以区域化内容匹配渠道服务，减少泛流量带来的无效成本。',
    icon: LibraryBig
  },
  {
    title: '高意向客户转化',
    desc: '以专业判断和真实案例建立信任，推动高意向客户主动咨询。',
    icon: Images
  }
]

const loadData = async () => {
  isLoading.value = true
  error.value = ''
  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') throw new Error('服务不可用')
    await infoStore.loadInfoConfig()
  } catch (loadError) {
    console.error('首页加载失败:', loadError)
    error.value = '后端服务暂时无法响应，请稍后重试。'
  } finally {
    isLoading.value = false
  }
}

const goTo = (path) => {
  if (!userStore.isLoggedIn) {
    sessionStorage.setItem('redirect', path)
    router.push({ path: '/login', query: { redirect: path } })
    return
  }
  router.push(path)
}

onMounted(() => {
  loadData()
  dashboardTimer = window.setInterval(() => {
    showAcquisitionMetrics.value = !showAcquisitionMetrics.value
  }, 3000)
})

onBeforeUnmount(() => window.clearInterval(dashboardTimer))
</script>

<style scoped lang="less" src="../assets/css/contentflow-homepage.less"></style>
