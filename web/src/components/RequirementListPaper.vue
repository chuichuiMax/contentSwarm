<script setup>
import { computed } from 'vue'
import { FORM_TITLE, QUESTIONS, completionOf } from '@/utils/requirementListForm'

const props = defineProps({
  answers: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
  lockStaff: { type: Boolean, default: false },
  paperTitle: { type: String, default: FORM_TITLE },
  questions: { type: Array, default: () => QUESTIONS },
})

const address = defineModel('address', { type: String, default: '' })
const customerName = defineModel('customerName', { type: String, default: '' })
const phone = defineModel('phone', { type: String, default: '' })
const staffCode = defineModel('staffCode', { type: String, default: '' })

defineEmits(['submit'])

const completion = computed(() => completionOf({
  answers: props.answers,
  customerName: customerName.value,
  phone: phone.value,
  address: address.value,
  questions: props.questions,
}))
</script>

<template>
  <div class="paper">
    <div class="paper-brand">
      <img class="logo" src="@/assets/hirun-logo.png" alt="鸿扬家装">
      <div class="slogan">高标准 高品质</div>
    </div>
    <h2>{{ paperTitle }}</h2>
    <p class="intro">尊敬的客户，您好！感谢您关注、考察鸿扬家装！</p>
    <p class="intro">为给您打造<span class="hl">好看耐住</span>的专属装修，我们将根据您的真实居住习惯、装修诉求和预算规划，做个性化定制设计，让每一笔投入都贴合您的生活需求。</p>
    <p class="intro">请您简单填写以下装修需求问卷，方便我们为您精准匹配合适的设计师，提前为您构思专属方案，让后续沟通更高效、方案更贴合您的期待！</p>
    <p class="intro">仅需<span class="mins">3-5分钟</span>，您的每一项需求，都是我们定制理想家的重要依据。</p>
    <p class="notice">个性记录篇：深度了解您的需求，才能做出适配您的设计方案，我们将严格保护您的个人隐私。以下内容您可选择填写，不便填写的项目直接留白即可。</p>

    <div class="base-row">
      <label>楼盘地址：<input v-model="address" class="txt" aria-label="楼盘地址" placeholder="请输入楼盘地址" :disabled="readonly"></label>
      <label>姓名：<input v-model="customerName" class="txt" aria-label="姓名" placeholder="请输入客户姓名" :disabled="readonly"></label>
      <label>手机号码：<input v-model="phone" class="txt" aria-label="手机号码" placeholder="请输入手机号码" :disabled="readonly"></label>
    </div>

    <div v-for="question in questions" :key="question.key" class="q">
      <div class="q-label">{{ question.title }}</div>
      <template v-if="question.kind === 'area'">
        <input v-model="answers.q2_area" class="txt full" aria-label="房屋面积" :disabled="readonly">
        <div class="q-label gap">房型：</div>
        <input v-model="answers.q2_layout" class="txt full" aria-label="房型" :disabled="readonly">
      </template>
      <template v-else-if="question.kind === 'text'">
        <input
          v-model="answers[question.key]"
          class="txt full"
          :aria-label="question.title"
          :placeholder="question.placeholder || question.title"
          :disabled="readonly"
        >
      </template>
      <div v-else-if="question.kind === 'groups'" class="sub-grid">
        <div v-for="(group, index) in question.groups" :key="group.label">
          <div class="sg-label">{{ group.label }}</div>
          <div class="opts">
            <label v-for="option in group.options" :key="option" class="opt">
              <input v-model="answers[`${question.key}_${index}`]" type="radio" :name="`${question.key}_${index}`" :value="option" :disabled="readonly">
              <span>{{ option }}</span>
            </label>
          </div>
        </div>
      </div>
      <div v-else class="opts">
        <label v-for="option in question.options" :key="option" class="opt" :class="{ box: question.kind === 'check' }">
          <input
            v-model="answers[question.key]"
            :type="question.kind === 'check' ? 'checkbox' : 'radio'"
            :name="question.key"
            :value="option"
            :disabled="readonly"
          >
          <span>{{ option }}</span>
        </label>
        <input v-if="question.other" v-model="answers[`${question.key}_other`]" class="inline-txt" aria-label="其他说明" :disabled="readonly">
      </div>
    </div>

    <div class="paper-foot">
      <div class="lbl">分享人工号</div>
      <input v-model="staffCode" class="txt" aria-label="分享人工号" :disabled="readonly || lockStaff">
      <button type="button" class="submit" @click="$emit('submit')">提交 {{ completion.percent }}%</button>
    </div>
  </div>
</template>

<style scoped lang="less">
.paper { width: 648px; max-width: 100%; margin: 0 auto; padding: 20px 34px 44px; background: #fff; box-shadow: 0 6px 26px rgba(31, 35, 41, .12); color: #2b2f36; font-size: 11px; }
.paper-brand { display: flex; justify-content: space-between; }
.logo { display: block; height: 42px; width: auto; }
.slogan { color: #9aa1ad; font-size: 8px; letter-spacing: 1px; }
.paper h2 { margin: 16px 0 18px; color: #c8162f; font-size: 19px; text-align: center; }
.intro { margin: 0 0 7px; line-height: 1.85; }
.hl, .mins { color: #c8162f; font-weight: 700; }
.notice { margin: 12px 0 20px; color: #d0342c; font-size: 10px; font-style: italic; line-height: 1.8; text-decoration: underline; }
.base-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 18px; label { color: #3a5a8c; font-size: 10px; } }
.txt { width: 100%; height: 22px; margin-top: 4px; padding: 0 6px; border: 1px solid #d9dee5; color: #3b4048; font: inherit; }
.q { margin-bottom: 17px; }
.q-label, .sg-label, .lbl { color: #3a5a8c; font-weight: 500; }
.q-label.gap { margin-top: 10px; }
.opts { display: flex; flex-wrap: wrap; gap: 7px 14px; align-items: center; }
.opt { display: inline-flex; align-items: center; gap: 4px; color: #3b4048; }
.full { width: 180px; }
.inline-txt { width: 110px; height: 19px; border: 1px solid #d9dee5; font: inherit; }
.sub-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 22px; }
.paper-foot .txt { width: 180px; height: 24px; }
.submit { height: 30px; margin-top: 18px; margin-left: 12px; padding: 0 16px; border: 0; border-radius: 2px; background: #4a6da5; color: #fff; white-space: nowrap; cursor: pointer; }
</style>
