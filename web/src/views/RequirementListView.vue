<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { requirementListApi } from '@/apis/requirement_list_api'
import { employeeApi } from '@/apis/employee_api'
import RequirementListPaper from '@/components/RequirementListPaper.vue'
import { FORM_TITLE, blankAnswers, completionOf, requirementFormPack, requirementFormPackByTypeName } from '@/utils/requirementListForm'
import { requirementListShareUrl } from '@/utils/requirementListUrl'

const COPY_TIP = '链接已复制，可在企业微信或微信进行发送'
const STORES = ['长沙芙蓉店', '长沙袁家岭店', '长沙河西店', '长沙星沙店']
const TYPES = [
  { id: 'rough', title: '毛坯房', formTitle: '一体化平面规划需求清单', description: '为了定制专属于您的理想生活方式，共同创造更加贴近您期望的设计方案。' },
  { id: 'decorated', title: '精装房', formTitle: '精装房定制需求表', description: '家的需求 + (a%) 过去生活习惯 + (b%) 现时生活需要 + (c%) 未来生活愿望' },
  { id: 'renovation', title: '旧房焕新', formTitle: '旧房焕新定制需求表', description: '为了居住更舒适，更适合您的改造需求，美好的家居生活，从懂您所需焕新需求开始。' }
]

const records = ref([])
const branches = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const branch = ref('all')
const submittedRange = ref('all')
const sharedRange = ref('all')
const applied = ref({ keyword: '', branch: 'all', submitted_range: 'all', shared_range: 'all' })
const loading = ref(false)
const freshId = ref('')
const employeeCode = ref('')
const mode = ref('')
const activeType = ref('')
const staffCode = ref('')
const customerName = ref('')
const phone = ref('')
const address = ref('')
const activePaperTitle = ref(FORM_TITLE)
const activeQuestions = ref(requirementFormPack('rough').questions)
const answers = reactive(blankAnswers(activeQuestions.value))
const toastText = ref('')
const toastVisible = ref(false)
const shareRecord = ref(null)
const shareKeyword = ref('')
const shareCandidates = ref([])
const shareSelected = ref([])
const shareLoading = ref(false)
let toastTimer = 0
let shareTimer = 0

const readonly = computed(() => mode.value === 'detail')
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const storeOptions = computed(() => [...STORES, ...branches.value.filter((item) => !STORES.includes(item))])
const shareLink = (type) => requirementListShareUrl(employeeCode.value, type, window.location.origin)
const shareUrl = computed(() => shareLink(activeType.value))
const completion = computed(() => completionOf({
  answers,
  customerName: customerName.value,
  phone: phone.value,
  address: address.value,
  questions: activeQuestions.value,
}))
const modalTitle = computed(() => (
  mode.value === 'fill' ? `开始填写 · ${FORM_TITLE}` : `需求清单详情 · ${FORM_TITLE}`
))
const modalSub = computed(() => (
  mode.value === 'fill' ? '当前页面遮罩内填写，未打开新网页' : '当前页面遮罩内查看，未打开新网页'
))

const showToast = (text) => {
  toastText.value = text
  toastVisible.value = true
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toastVisible.value = false }, 3200)
}

const filters = () => ({
  keyword: applied.value.keyword,
  branch: applied.value.branch === 'all' ? undefined : applied.value.branch,
  submitted_range: applied.value.submitted_range,
  shared_range: applied.value.shared_range
})

const load = async () => {
  loading.value = true
  try {
    const response = await requirementListApi.list({ ...filters(), page: page.value, page_size: pageSize.value })
    records.value = response.items || []
    total.value = response.total || 0
    branches.value = response.branches || []
  } catch (error) {
    records.value = []
    total.value = 0
    showToast(error.message || '需求清单暂时无法加载')
  } finally {
    loading.value = false
  }
}

const search = async () => {
  applied.value = {
    keyword: keyword.value.trim(),
    branch: branch.value,
    submitted_range: submittedRange.value,
    shared_range: sharedRange.value
  }
  page.value = 1
  await load()
  showToast(`已按条件查询，匹配 ${total.value} 条需求清单`)
}

const changePage = (next) => {
  const target = Math.min(pageCount.value, Math.max(1, next))
  if (target === page.value) return
  page.value = target
  void load()
}

const resetAnswers = (source = {}, questionList = activeQuestions.value) => {
  const next = blankAnswers(questionList)
  for (const key of Object.keys(next)) {
    if (Array.isArray(next[key])) next[key] = Array.isArray(source[key]) ? [...source[key]] : []
    else if (source[key] != null) next[key] = String(source[key])
  }
  Object.assign(answers, next)
}

const applyFormPack = (pack, formTitle = pack.introTitle) => {
  activePaperTitle.value = formTitle || pack.introTitle
  activeQuestions.value = pack.questions
}

const openFill = (item) => {
  mode.value = 'fill'
  activeType.value = item.title
  const pack = requirementFormPack(item.id || 'rough')
  applyFormPack(pack, item.formTitle)
  customerName.value = ''
  phone.value = ''
  address.value = ''
  staffCode.value = employeeCode.value
  resetAnswers({}, pack.questions)
}

const openDetail = async (record) => {
  try {
    const detail = await requirementListApi.detail(record.id)
    mode.value = 'detail'
    activeType.value = detail.requirement_type
    const pack = requirementFormPackByTypeName(detail.requirement_type)
    applyFormPack(pack)
    customerName.value = detail.customer_name || ''
    phone.value = detail.phone || ''
    address.value = detail.address || ''
    staffCode.value = detail.employee_code || ''
    resetAnswers(detail.answers || {}, pack.questions)
  } catch (error) {
    showToast(error.message || '需求详情暂时无法加载')
  }
}

const closeModal = () => { mode.value = '' }

const copyText = async (text) => {
  if (!text) {
    showToast('当前账号没有员工编码，无法分享需求清单')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    area.remove()
  }
  showToast(COPY_TIP)
}

const submitForm = async () => {
  if (readonly.value) {
    showToast('详情为只读查看，表单不会再次提交')
    return
  }
  const name = customerName.value.trim()
  const mobile = phone.value.trim()
  const place = address.value.trim()
  if (!name && !mobile && !place) {
    showToast('请先填写客户姓名、手机号码或楼盘地址')
    return
  }
  try {
    const created = await requirementListApi.create({
      requirement_type: activeType.value || '毛坯房',
      customer_name: name,
      phone: mobile,
      address: place,
      staff_code: staffCode.value.trim(),
      answers: JSON.parse(JSON.stringify(answers))
    })
    freshId.value = created.id
    closeModal()
    page.value = 1
    await load()
    showToast('提交成功，客户信息已写入需求清单列表')
  } catch (error) {
    showToast(error.message || '提交失败')
  }
}

const loadShareCandidates = async () => {
  shareLoading.value = true
  try {
    const response = await requirementListApi.shareEmployees({ keyword: shareKeyword.value.trim() })
    shareCandidates.value = response.items || []
  } catch (error) {
    shareCandidates.value = []
    showToast(error.message || '员工列表暂时无法加载')
  } finally {
    shareLoading.value = false
  }
}

const openShare = (record) => {
  shareRecord.value = record
  shareKeyword.value = ''
  shareSelected.value = []
  void loadShareCandidates()
}

const closeShare = () => {
  shareRecord.value = null
  shareKeyword.value = ''
  shareCandidates.value = []
  shareSelected.value = []
}

const toggleShareEmployee = (employeeId) => {
  const next = [...shareSelected.value]
  const index = next.indexOf(employeeId)
  if (index >= 0) next.splice(index, 1)
  else next.push(employeeId)
  shareSelected.value = next
}

const submitShare = async () => {
  if (!shareRecord.value) return
  const picked = shareCandidates.value.filter((item) => shareSelected.value.includes(item.id))
  if (!picked.length) {
    showToast('请至少选择一位员工')
    return
  }
  try {
    const updated = await requirementListApi.share(shareRecord.value.id, { employee_ids: picked.map((item) => item.id) })
    const index = records.value.findIndex((item) => item.id === shareRecord.value.id)
    if (index >= 0) {
      records.value[index] = { ...records.value[index], ...updated }
    }
    closeShare()
    showToast('共享成功，所选员工已可查看该条需求清单')
  } catch (error) {
    showToast(error.message || '共享失败')
  }
}

const onShareKeywordInput = () => {
  clearTimeout(shareTimer)
  shareTimer = window.setTimeout(() => { void loadShareCandidates() }, 280)
}

const removeRow = async (record) => {
  try {
    await requirementListApi.remove(record.id)
    if (records.value.length === 1 && page.value > 1) page.value -= 1
    await load()
    showToast('已删除该条需求清单')
  } catch (error) {
    showToast(error.message || '删除失败')
  }
}

const exportRows = async () => {
  try {
    const response = await requirementListApi.export(filters())
    const blob = await response.blob()
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = '需求清单.csv'
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (error) {
    showToast(error.message || '导出失败')
  }
}

const onKeydown = (event) => {
  if (event.key === 'Escape') closeModal()
}

const textOf = (value) => value || '—'

onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  void load()
  try {
    const response = await employeeApi.getMyEmployee()
    employeeCode.value = response.employee?.employee_code || ''
  } catch {
    employeeCode.value = ''
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  clearTimeout(toastTimer)
  clearTimeout(shareTimer)
})
</script>

<template>
  <div class="requirement-page">
    <section class="section">
      <h2>需求清单</h2>
      <p>用户真实需求，设计符合客户需求的家</p>
    </section>

    <div class="cards">
      <article v-for="item in TYPES" :key="item.title" class="card">
        <div class="card-top">
          <h3>{{ item.title }}</h3>
          <div class="card-actions">
            <button type="button" class="start-btn" @click="openFill(item)">开始填写</button>
            <button type="button" class="share-btn" @click="copyText(shareLink(item.title))">分享</button>
          </div>
        </div>
        <div class="sub">{{ item.formTitle }}</div>
        <div class="desc">{{ item.description }}</div>
      </article>
    </div>

    <section class="panel">
      <div class="filters">
        <label class="field search">
          <input v-model="keyword" aria-label="搜索关键词" placeholder="输入搜索关键词" @keydown.enter="search">
        </label>
        <label class="field select">
          <select v-model="branch" aria-label="所属门店">
            <option value="all">所属门店</option>
            <option v-for="item in storeOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label class="field select">
          <select v-model="submittedRange" aria-label="提交时间">
            <option value="all">提交时间</option>
            <option value="today">今天</option>
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
          </select>
        </label>
        <label class="field select">
          <select v-model="sharedRange" aria-label="分享时间">
            <option value="all">分享时间</option>
            <option value="today">今天</option>
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
          </select>
        </label>
        <button type="button" class="primary-sm" @click="search">查询</button>
        <button type="button" class="primary-sm" @click="exportRows">导出</button>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>序</th><th>需求清单类型</th><th>客户姓名</th><th>手机号码</th><th>楼盘地址</th>
              <th>分享人</th><th>岗位</th><th>工号</th><th>完成度</th><th>所属门店</th>
              <th>分享时间</th><th>提交时间</th><th>提交人</th><th>共享成员</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(record, index) in records" :key="record.id" :class="{ fresh: record.id === freshId }">
              <td class="num">{{ (page - 1) * pageSize + index + 1 }}</td>
              <td>{{ record.requirement_type }}</td>
              <td>{{ textOf(record.customer_name) }}</td>
              <td class="num">{{ textOf(record.phone) }}</td>
              <td>{{ textOf(record.address) }}</td>
              <td>{{ textOf(record.sharer_name) }}</td>
              <td>{{ textOf(record.role) }}</td>
              <td class="num">{{ textOf(record.employee_code) }}</td>
              <td class="done">{{ record.progress }}</td>
              <td>{{ textOf(record.branch) }}</td>
              <td class="num">{{ record.shared_at }}</td>
              <td class="num">{{ record.submitted_at }}</td>
              <td>{{ textOf(record.submitter_name) }}</td>
              <td class="members">
                <span v-if="!record.shared_members?.length" class="muted">未共享</span>
                <span v-else v-for="member in record.shared_members" :key="member.uid" class="member-chip">
                  <span class="tag">企微</span>{{ member.name }}
                </span>
              </td>
              <td>
                <button type="button" class="detail-btn" @click="openDetail(record)">查看详情</button>
                <button
                  v-if="record.can_share"
                  type="button"
                  class="detail-btn"
                  @click="openShare(record)"
                >共享</button>
                <button v-if="record.can_share" type="button" class="del" @click="removeRow(record)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!loading && !records.length" class="empty">没有符合当前查询条件的需求清单</div>
      </div>

      <div class="pager">
        <span>每页显示 {{ pageSize }} 条 - 共 {{ total }} 条</span>
        <div class="pages">
          <button type="button" class="outline" @click="changePage(page - 1)">上一页</button>
          <button
            v-for="item in pageCount"
            v-show="item === 1 || item === pageCount || Math.abs(item - page) <= 1"
            :key="item"
            type="button"
            :class="{ on: item === page }"
            @click="changePage(item)"
          >{{ item }}</button>
          <button type="button" class="outline" @click="changePage(page + 1)">下一页</button>
        </div>
      </div>
    </section>

    <div class="toast" :class="{ show: toastVisible }" role="status">{{ toastText }}</div>

    <div v-if="shareRecord" class="overlay show" @click.self="closeShare">
      <div class="share-dialog" role="dialog" aria-modal="true" aria-label="共享需求清单">
        <div class="share-head">
          <h3>共享需求清单</h3>
          <button type="button" class="icon-btn" aria-label="关闭" @click="closeShare">×</button>
        </div>
        <p class="share-tip">选择同事后，对方可在需求清单列表中查看本条记录。</p>
        <label class="share-search">
          <input
            v-model="shareKeyword"
            aria-label="搜索员工"
            placeholder="输入姓名、工号或手机号"
            @input="onShareKeywordInput"
          >
        </label>
        <div class="share-list" :aria-busy="shareLoading">
          <label v-for="employee in shareCandidates" :key="employee.id" class="share-item">
            <input
              type="checkbox"
              :checked="shareSelected.includes(employee.id)"
              @change="toggleShareEmployee(employee.id)"
            >
            <span class="share-name">{{ employee.name }}</span>
            <span class="share-meta">{{ employee.employee_code }} · {{ employee.branch || '—' }}</span>
          </label>
          <div v-if="!shareLoading && !shareCandidates.length" class="share-empty">没有匹配的员工</div>
        </div>
        <div class="share-foot">
          <button type="button" class="btn plain" @click="closeShare">取消</button>
          <button type="button" class="btn primary" @click="submitShare">确认共享</button>
        </div>
      </div>
    </div>

    <div v-if="mode" class="overlay show" @click.self="closeModal">
      <div class="modal" role="dialog" aria-modal="true" :aria-label="modalTitle">
        <div class="modal-head">
          <div class="ht">
            <h3>{{ modalTitle }}</h3>
            <span class="hsub">{{ modalSub }}</span>
          </div>
          <button type="button" class="icon-btn" aria-label="关闭" @click="closeModal">×</button>
        </div>
        <div class="form-view">
          <RequirementListPaper
            v-model:address="address"
            v-model:customer-name="customerName"
            v-model:phone="phone"
            v-model:staff-code="staffCode"
            :answers="answers"
            :readonly="readonly"
            :paper-title="activePaperTitle"
            :questions="activeQuestions"
            @submit="submitForm"
          />
        </div>
        <div class="modal-foot">
          <div class="src">
            <code>{{ shareUrl || '当前账号没有员工编码' }}</code>
            <button type="button" class="btn soft" @click="copyText(shareUrl)">复制链接</button>
          </div>
          <div class="foot-actions">
            <button v-if="mode === 'fill'" type="button" class="btn primary" @click="submitForm">提交并生成清单 {{ completion.percent }}%</button>
            <button type="button" class="btn plain" @click="closeModal">关闭</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.requirement-page { min-height: 100%; padding: 24px; background: #f5f6f8; color: #1f2329; }
.section h2 { margin: 0; font-size: 22px; }
.section p { margin: 6px 0 0; color: #8a9099; }
.cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 16px 0; }
.card { display: flex; flex-direction: column; gap: 8px; min-height: 140px; padding: 16px; border: 1px solid #e9ecf2; border-radius: 8px; background: #fff; }
.card-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; h3 { margin: 0; font-size: 15px; } }
.card-actions { display: flex; gap: 8px; }
.start-btn, .share-btn, .primary-sm, .btn { border: 0; cursor: pointer; }
.start-btn, .share-btn { height: 26px; padding: 0 12px; border-radius: 4px; font-size: 11px; }
.start-btn { background: #3d6bff; color: #fff; }
.share-btn { background: #edf1ff; color: #3d6bff; }
.sub { color: #3b4048; }
.desc { margin-top: auto; color: #8fb99b; font-size: 11px; line-height: 1.65; }
.panel { display: flex; flex-direction: column; min-height: 420px; overflow: hidden; border: 1px solid #e9ecf2; border-radius: 8px; background: #fff; }
.filters { display: flex; align-items: center; gap: 12px; height: 64px; padding: 0 16px; }
.field { display: flex; align-items: center; height: 34px; padding: 0 10px; border: 1px solid #e2e6ef; border-radius: 4px; color: #6b7280; }
.field.search { width: 220px; }
.field.select { width: 170px; }
.field input, .field select { width: 100%; border: 0; outline: none; background: transparent; color: inherit; font: inherit; }
.primary-sm { height: 34px; padding: 0 16px; border-radius: 4px; background: #3d6bff; color: #fff; font-size: 12px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { height: 44px; padding: 0 8px; background: #f7f9fc; color: #8a9099; font-weight: 400; text-align: left; white-space: nowrap; }
td { height: 60px; max-width: 160px; padding: 0 8px; overflow: hidden; border-bottom: 1px solid #f0f1f5; color: #3b4048; text-overflow: ellipsis; white-space: nowrap; }
tr.fresh { background: #eaf1ff; }
.num { font-family: Arial, sans-serif; }
.done { color: #22a06b; font-weight: 600; }
.detail-btn, .del { border: 0; background: none; cursor: pointer; font-size: 12px; }
.detail-btn { color: #2f6bff; }
.del { margin-left: 10px; color: #f2544b; }
.members { max-width: 160px; white-space: normal; }
.muted { color: #9aa1ad; }
.member-chip { display: inline-flex; align-items: center; gap: 4px; margin: 0 6px 4px 0; font-size: 11px; }
.member-chip .tag { padding: 0 4px; border-radius: 2px; background: #e8f1ff; color: #2f6bff; font-size: 10px; }
.share-dialog { display: flex; flex-direction: column; width: 520px; max-width: calc(100vw - 48px); max-height: min(78vh, 640px); overflow: hidden; border-radius: 12px; background: #fff; }
.share-head, .share-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px; }
.share-head { border-bottom: 1px solid #e9ecf2; h3 { margin: 0; font-size: 15px; } }
.share-tip { margin: 0; padding: 0 20px; color: #8a9099; font-size: 12px; }
.share-search { display: block; padding: 12px 20px 0; input { width: 100%; height: 34px; padding: 0 10px; border: 1px solid #e2e6ef; border-radius: 4px; font: inherit; } }
.share-list { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 20px; }
.share-item { display: grid; grid-template-columns: 16px 1fr; gap: 6px 10px; align-items: center; padding: 8px 0; border-bottom: 1px solid #f3f4f7; cursor: pointer; }
.share-name { color: #1f2329; font-size: 13px; }
.share-meta { grid-column: 2; color: #9aa1ad; font-size: 11px; }
.share-empty { padding: 24px 0; color: #9aa1ad; text-align: center; font-size: 12px; }
.share-foot { justify-content: flex-end; border-top: 1px solid #e9ecf2; }
.empty { padding: 36px; color: #9aa1ad; text-align: center; }
.pager { display: flex; align-items: flex-end; justify-content: space-between; padding: 16px; color: #9aa1ad; font-size: 12px; }
.pages { display: flex; gap: 8px; }
.pages button { min-width: 28px; height: 28px; padding: 0 10px; border: 1px solid transparent; border-radius: 4px; background: none; color: #8a9099; cursor: pointer; }
.pages button.outline { border-color: #e2e6ef; }
.pages button.on { background: #3d6bff; color: #fff; }
.toast { position: fixed; top: 20px; left: 50%; z-index: 60; min-width: 420px; padding: 14px 18px; border-radius: 10px; background: #1f2329; color: #fff; opacity: 0; pointer-events: none; transform: translate(-50%, -16px); transition: .22s ease; }
.toast.show { opacity: 1; transform: translate(-50%, 0); }
.overlay { position: fixed; inset: 0; z-index: 50; display: flex; align-items: center; justify-content: center; background: rgba(20, 24, 32, .52); }
.modal { display: flex; flex-direction: column; width: 920px; max-width: calc(100vw - 48px); height: min(86vh, 840px); overflow: hidden; border-radius: 14px; background: #fff; }
.modal-head, .modal-foot { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 0 20px; }
.modal-head { height: 54px; border-bottom: 1px solid #e9ecf2; }
.ht { display: flex; align-items: baseline; gap: 12px; h3 { margin: 0; font-size: 15px; } }
.hsub { color: #9aa1ad; font-size: 11px; }
.icon-btn { width: 28px; height: 28px; border: 0; border-radius: 6px; background: none; color: #8a9099; cursor: pointer; font-size: 20px; }
.form-view { flex: 1; min-height: 0; overflow-y: auto; padding: 22px; background: linear-gradient(120deg, #f2efe7 0%, #eae7ee 26%, #dee8f2 55%, #e6ece6 76%, #f5eee4 100%); }
.modal-foot { min-height: 58px; height: auto; padding: 12px 20px; border-top: 1px solid #e9ecf2; }
.src { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; color: #8a9099; font-size: 11px; code { flex: 1; min-width: 0; overflow: hidden; color: #5a6270; text-overflow: ellipsis; white-space: nowrap; } }
.foot-actions { display: flex; align-items: center; gap: 8px; flex: none; }
.btn { height: 32px; padding: 0 14px; border-radius: 5px; font-size: 12px; white-space: nowrap; flex: none; }
.btn.primary { background: #3d6bff; color: #fff; }
.btn.soft { background: #edf1ff; color: #3d6bff; }
.btn.plain { background: none; color: #8a9099; }
</style>
