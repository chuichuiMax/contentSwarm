<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import RequirementListPaper from '@/components/RequirementListPaper.vue'
import { requirementListApi } from '@/apis/requirement_list_api'
import { blankAnswers, requirementFormPackByTypeName } from '@/utils/requirementListForm'

const TYPES = ['毛坯房', '精装房', '旧房焕新']
const route = useRoute()
const requirementType = computed(() => String(route.query.type || '').trim())
const staffCode = computed(() => String(route.query.usercode || '').trim())
const valid = computed(() => TYPES.includes(requirementType.value) && Boolean(staffCode.value))
const formPack = computed(() => requirementFormPackByTypeName(requirementType.value))
const paperTitle = computed(() => formPack.value.introTitle)
const questions = computed(() => formPack.value.questions)

const customerName = ref('')
const phone = ref('')
const address = ref('')
const answers = reactive({})
watch(questions, (list) => {
  Object.assign(answers, blankAnswers(list))
}, { immediate: true })
const done = ref(false)
const message = ref('')

const submit = async () => {
  message.value = ''
  try {
    await requirementListApi.submitPublic({
      requirement_type: requirementType.value,
      customer_name: customerName.value,
      phone: phone.value,
      address: address.value,
      staff_code: staffCode.value,
      answers: { ...answers },
    })
    done.value = true
  } catch (error) {
    message.value = error?.message || '提交失败，请稍后重试'
  }
}
</script>

<template>
  <main class="share-page">
    <p v-if="!valid" class="result">分享链接无效，请向分享人重新获取链接。</p>
    <p v-else-if="done" class="result">提交成功，客户信息已写入需求清单列表</p>
    <template v-else>
      <RequirementListPaper
        v-model:address="address"
        v-model:customer-name="customerName"
        v-model:phone="phone"
        :staff-code="staffCode"
        :answers="answers"
        :paper-title="paperTitle"
        :questions="questions"
        lock-staff
        @submit="submit"
      />
      <p v-if="message" class="error">{{ message }}</p>
    </template>
  </main>
</template>

<style scoped lang="less">
.share-page {
  min-height: 100vh;
  padding: 22px;
  background: linear-gradient(120deg, #f2efe7 0%, #eae7ee 26%, #dee8f2 55%, #e6ece6 76%, #f5eee4 100%);
}
.result, .error {
  width: 648px;
  max-width: 100%;
  margin: 48px auto;
  padding: 28px 24px;
  background: #fff;
  box-shadow: 0 6px 26px rgba(31, 35, 41, .12);
  color: #2b2f36;
  text-align: center;
}
.error { margin-top: 12px; color: #d0342c; }
</style>
