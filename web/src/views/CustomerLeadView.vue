<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import { customerLeadApi } from '@/apis/customer_lead_api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const loading = ref(false)
const loadError = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
let version = 0
const canDelete = computed(() => userStore.can('customer_lead.delete'))
const pagination = computed(() => ({
  current: page.value, pageSize: pageSize.value, total: total.value,
  showSizeChanger: true, showTotal: (count) => `共 ${count} 条`, pageSizeOptions: ['10', '20', '50']
}))
const columns = [
  { title: '序号', key: 'index', width: 76 },
  { title: '头像', key: 'avatar', width: 92 },
  { title: '客户姓名', dataIndex: 'nickname', key: 'nickname', width: 180, ellipsis: true },
  { title: '联系电话', dataIndex: 'phone', key: 'phone', width: 180 },
  { title: '来源', key: 'source', width: 220, ellipsis: true },
  { title: '获取时间', key: 'time', width: 190 },
  { title: '操作', key: 'action', width: 96 }
]

function releaseAvatars(rows) {
  for (const row of rows) if (row.avatarSrc) URL.revokeObjectURL(row.avatarSrc)
}

async function load() {
  const current = ++version
  loading.value = true
  loadError.value = ''
  try {
    const response = await customerLeadApi.list(page.value, pageSize.value)
    if (current !== version) return
    releaseAvatars(items.value)
    items.value = response.items.map((row) => ({ ...row, avatarSrc: '', avatarError: false }))
    total.value = response.total
    // 头像通过鉴权请求读取，不把完整客资或凭证放进公开图片地址。
    await Promise.all(items.value.map(async (row) => {
      if (!row.avatar_url) return
      try {
        const response = await customerLeadApi.avatar(row.id)
        const blob = await response.blob()
        if (current === version) row.avatarSrc = URL.createObjectURL(blob)
      } catch {
        if (current === version) row.avatarError = true
      }
    }))
  } catch (error) {
    if (current === version) {
      releaseAvatars(items.value)
      items.value = []
      loadError.value = error.message || '加载客资失败'
    }
  } finally {
    if (current === version) loading.value = false
  }
}

function changePage(value) {
  page.value = value.current
  pageSize.value = value.pageSize
  void load()
}

function remove(row) {
  Modal.confirm({
    title: '确认删除这条客资？', content: `将删除「${row.nickname || '预约客户'}」的本次预约信息，案例和其他预约不受影响。`,
    okText: '删除', cancelText: '取消', okType: 'danger',
    async onOk() {
      try {
        await customerLeadApi.delete(row.id)
        message.success('已删除')
        if (items.value.length === 1 && page.value > 1) page.value -= 1
        await load()
      } catch (error) {
        message.error(error.message || '删除失败')
        throw error
      }
    }
  })
}

function formatTime(value) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date(value))
}

onMounted(load)
onBeforeUnmount(() => { version += 1; releaseAvatars(items.value) })
</script>

<template>
  <div class="customer-lead-page">
    <PageHeader title="客资报表" :loading="loading">
      <template #actions>
        <a-button :loading="loading" @click="load"><RefreshCw :size="15" />刷新</a-button>
      </template>
    </PageHeader>
    <main class="report-content">
      <a-result v-if="loadError" status="error" title="客资暂时无法加载" :sub-title="loadError">
        <template #extra><a-button type="primary" @click="load">重新加载</a-button></template>
      </a-result>
      <a-table v-else :columns="columns" :data-source="items" :loading="loading" row-key="id"
        :pagination="pagination" :scroll="{ x: 1054 }" :locale="{ emptyText: '暂无参观预约' }" @change="changePage">
        <template #bodyCell="{ column, record, index }">
          <template v-if="column.key === 'index'">{{ (page - 1) * pageSize + index + 1 }}</template>
          <template v-else-if="column.key === 'avatar'">
            <img v-if="record.avatarSrc" class="customer-avatar" :src="record.avatarSrc" alt="客户头像" />
            <span v-else class="avatar-placeholder">{{ !record.avatar_url ? '客' : (record.avatarError ? '加载失败' : '加载中') }}</span>
          </template>
          <template v-else-if="column.key === 'nickname'">{{ record.nickname || '预约客户' }}</template>
          <template v-else-if="column.key === 'source'">
            <a :href="record.source_url" target="_blank" rel="noopener noreferrer" :title="record.source_title">
              {{ record.source_title }}
            </a>
          </template>
          <template v-else-if="column.key === 'time'">{{ formatTime(record.acquired_at) }}</template>
          <template v-else-if="column.key === 'action'">
            <a-button v-if="canDelete" type="link" danger size="small" @click="remove(record)">删除</a-button>
          </template>
        </template>
      </a-table>
    </main>
  </div>
</template>

<style scoped lang="less">
.customer-lead-page { min-height: 100%; background: var(--gray-25); }
.report-content { padding: 24px var(--page-padding); }
.customer-avatar, .avatar-placeholder { width: 44px; height: 44px; border-radius: 50%; }
.customer-avatar { object-fit: cover; display: block; }
.avatar-placeholder { display: inline-flex; align-items: center; justify-content: center;
  background: var(--gray-100); color: var(--gray-600); font-size: 10px; }
:deep(.ant-table-cell) { font-variant-numeric: tabular-nums; }
:deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }
</style>
