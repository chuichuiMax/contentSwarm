<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  type: { type: String, default: 'methods' },
  item: { type: Object, default: null },
  methodOptions: { type: Array, default: () => [] },
  titleOptions: { type: Array, default: () => [] },
  contentOptions: { type: Array, default: () => [] },
  contentTypeOptions: { type: Array, default: () => [] }
})

const emit = defineEmits(['close', 'save'])
const formRef = ref()
const form = reactive({})

const typeMeta = computed(() => ({
  methods: { title: '创作手法', codePrefix: 'M' },
  title_formulas: { title: '标题公式', codePrefix: 'T' },
  content_formulas: { title: '正文公式', codePrefix: 'C' },
  combination_rules: { title: '组合规则', codePrefix: '' }
})[props.type])
const isNew = computed(() => !props.item)
const isProtectedMethod = computed(() => props.type === 'methods' && form.code === 'S01')

const defaults = () => {
  if (props.type === 'methods') {
    return {
      code: '', name: '', method_type: 'core', principle: '', suitable_scenes: [],
      sentence_patterns: [], tag_schema: {}, variable_schema: [], risk_rules: [], enabled: true
    }
  }
  if (props.type === 'title_formulas') {
    return {
      code: '', name: '', suitable_scenes: [], core_goal: '', reference_examples: [],
      variable_schema: [], compatible_methods: [], risk_rules: [], enabled: true
    }
  }
  if (props.type === 'content_formulas') {
    return {
      code: '', name: '', industry_aliases: {}, compatible_methods: [], suitable_scenes: [],
      business_pains: [], structure_schema: [''], reference_examples: [], required_variables: [],
      output_schema: {}, risk_rules: [], enabled: true
    }
  }
  return {
    schema_version: 3,
    content_goal_codes: [],
    content_type_codes: [],
    industry_scope: [],
    channel_scope: [],
    narrative_axis_codes: [],
    combination_type: 'single',
    method_members: [],
    title_formula_candidate_codes: [],
    body_formula_candidate_codes: [],
    scenario_description: '',
    required_variable_codes: [],
    required_evidence_types: [],
    priority: 100,
    conditions: {},
    hard_conditions: {},
    score_weights: {},
    fallback_rule_id: null,
    source_metadata: {},
    recommendation_reason: ''
  }
}

const combinationMethodCodes = computed({
  get: () => (form.method_members || []).map((item) => item.method_code),
  set: (codes) => {
    form.method_members = codes.map((methodCode, index) => ({
      method_code: methodCode,
      role: index === 0 ? 'primary' : 'supporting',
      order: index + 1
    }))
    form.combination_type = ['single', 'double', 'triple', 'quadruple'][codes.length - 1] || 'single'
  }
})

watch(
  () => [props.open, props.type, props.item],
  () => {
    if (!props.open) return
    Object.keys(form).forEach((key) => delete form[key])
    Object.assign(form, structuredClone(props.item || defaults()))
  },
  { immediate: true }
)

const rules = computed(() => {
  if (props.type === 'combination_rules') {
    return {
      content_type_codes: [{ required: true, type: 'array', min: 1, message: '至少选择一个内容方向' }],
      method_members: [{ required: true, type: 'array', min: 1, max: 4, message: '请选择 1～4 个创作手法' }],
      title_formula_candidate_codes: [{ required: true, type: 'array', min: 1, message: '至少选择一个标题公式' }],
      body_formula_candidate_codes: [{ required: true, type: 'array', min: 1, message: '至少选择一个正文公式' }],
      scenario_description: [{ required: true, message: '请填写本组适用场景' }]
    }
  }
  const result = {
    code: [
      { required: true, message: '请输入编码' },
      { pattern: /^[A-Za-z][A-Za-z0-9_-]*$/, message: '编码需以字母开头，仅使用字母、数字、_ 或 -' }
    ],
    name: [{ required: true, message: '请输入名称' }]
  }
  if (props.type === 'methods') result.principle = [{ required: true, message: '请输入核心原则' }]
  if (props.type === 'title_formulas') {
    result.core_goal = [{ required: true, message: '请输入核心目标' }]
    result.compatible_methods = [{ required: true, type: 'array', min: 1, message: '至少选择一个核心手法' }]
  }
  if (props.type === 'content_formulas') {
    result.structure_schema = [{ required: true, type: 'array', min: 1, message: '至少添加一个正文段落' }]
    result.compatible_methods = [{ required: true, type: 'array', min: 1, message: '至少选择一个核心手法' }]
  }
  return result
})

const addStructureSection = () => form.structure_schema.push('')
const removeStructureSection = (index) => {
  if (form.structure_schema.length > 1) form.structure_schema.splice(index, 1)
}

const submit = async () => {
  await formRef.value?.validate()
  const value = structuredClone(form)
  if (value.code) value.code = value.code.trim().toUpperCase()
  if (value.structure_schema) {
    value.structure_schema = value.structure_schema.map((item) => item.trim()).filter(Boolean)
    if (!value.structure_schema.length) {
      message.error('至少填写一个有效的正文段落')
      return
    }
  }
  emit('save', value)
}
</script>

<template>
  <a-drawer
    :open="open"
    :width="560"
    :title="`${isNew ? '新增' : '编辑'}${typeMeta.title}`"
    :mask-closable="false"
    @close="emit('close')"
  >
    <a-alert
      v-if="!isNew && type !== 'combination_rules'"
      class="drawer-alert"
      type="info"
      show-icon
      message="规则编码已锁定"
      description="编码用于公式引用。为避免组合关系失效，已有规则只允许修改业务内容。"
    />

    <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
      <template v-if="type !== 'combination_rules'">
        <div class="field-row">
          <a-form-item label="规则编码" name="code">
            <a-input v-model:value="form.code" :disabled="!isNew" :placeholder="`${typeMeta.codePrefix}01`" />
          </a-form-item>
          <a-form-item label="名称" name="name">
            <a-input v-model:value="form.name" placeholder="让运营人员一眼识别用途" />
          </a-form-item>
        </div>
      </template>

      <template v-if="type === 'methods'">
        <div class="field-row">
          <a-form-item label="类型" name="method_type">
            <a-select v-model:value="form.method_type" :disabled="isProtectedMethod">
              <a-select-option value="core">核心手法</a-select-option>
              <a-select-option value="enhancer">场景增强器</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item label="启用状态">
            <a-switch v-model:checked="form.enabled" :disabled="isProtectedMethod" checked-children="启用" un-checked-children="停用" />
          </a-form-item>
        </div>
        <a-form-item label="核心原则" name="principle">
          <a-textarea v-model:value="form.principle" :rows="3" placeholder="说明这套手法解决什么问题、如何发挥作用" />
        </a-form-item>
        <a-form-item label="适用场景">
          <a-select v-model:value="form.suitable_scenes" mode="tags" placeholder="输入场景后按回车，例如：案例复盘" />
        </a-form-item>
        <a-form-item label="常用句式">
          <a-select v-model:value="form.sentence_patterns" mode="tags" placeholder="输入完整句式后按回车，变量使用 {name}" />
        </a-form-item>
        <a-form-item label="所需变量">
          <a-select v-model:value="form.variable_schema" mode="tags" placeholder="例如：number、result" />
        </a-form-item>
        <a-form-item label="风险规则">
          <a-select v-model:value="form.risk_rules" mode="tags" placeholder="输入一条风险约束后按回车" />
        </a-form-item>
      </template>

      <template v-else-if="type === 'title_formulas'">
        <a-form-item label="核心目标" name="core_goal">
          <a-textarea v-model:value="form.core_goal" :rows="3" placeholder="说明该标题公式主要提升什么" />
        </a-form-item>
        <a-form-item label="适用场景">
          <a-select v-model:value="form.suitable_scenes" mode="tags" placeholder="输入场景后按回车" />
        </a-form-item>
        <a-form-item label="兼容创作手法" name="compatible_methods">
          <a-select v-model:value="form.compatible_methods" mode="multiple" placeholder="选择至少一个核心手法">
            <a-select-option v-for="item in methodOptions" :key="item.code" :value="item.code">{{ item.code }} · {{ item.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="标题变量">
          <a-select v-model:value="form.variable_schema" mode="tags" placeholder="例如：audience、number、result" />
        </a-form-item>
        <a-form-item label="参考示例">
          <a-select v-model:value="form.reference_examples" mode="tags" placeholder="输入一条完整标题后按回车" />
        </a-form-item>
        <a-form-item label="风险规则">
          <a-select v-model:value="form.risk_rules" mode="tags" placeholder="输入一条风险约束后按回车" />
        </a-form-item>
        <a-form-item label="启用状态">
          <a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" />
        </a-form-item>
      </template>

      <template v-else-if="type === 'content_formulas'">
        <a-form-item label="正文结构" name="structure_schema" required>
          <div class="structure-list">
            <div v-for="(_, index) in form.structure_schema" :key="index">
              <span>{{ index + 1 }}</span>
              <a-input v-model:value="form.structure_schema[index]" placeholder="例如：用户痛点" />
              <button type="button" class="lucide-icon-btn" :disabled="form.structure_schema.length === 1" @click="removeStructureSection(index)"><Trash2 :size="16" /></button>
            </div>
            <a-button block @click="addStructureSection"><Plus :size="15" />添加段落</a-button>
          </div>
        </a-form-item>
        <a-form-item label="兼容创作手法" name="compatible_methods">
          <a-select v-model:value="form.compatible_methods" mode="multiple" placeholder="选择至少一个核心手法">
            <a-select-option v-for="item in methodOptions" :key="item.code" :value="item.code">{{ item.code }} · {{ item.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="适用场景">
          <a-select v-model:value="form.suitable_scenes" mode="tags" placeholder="输入场景后按回车" />
        </a-form-item>
        <a-form-item label="业务痛点">
          <a-select v-model:value="form.business_pains" mode="tags" placeholder="输入一个痛点后按回车" />
        </a-form-item>
        <a-form-item label="必需变量">
          <a-select v-model:value="form.required_variables" mode="tags" placeholder="例如：product、pain_points" />
        </a-form-item>
        <a-form-item label="参考示例">
          <a-select v-model:value="form.reference_examples" mode="tags" placeholder="输入一条示例后按回车" />
        </a-form-item>
        <a-form-item label="风险规则">
          <a-select v-model:value="form.risk_rules" mode="tags" placeholder="输入一条风险约束后按回车" />
        </a-form-item>
        <a-form-item label="启用状态">
          <a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" />
        </a-form-item>
      </template>

      <template v-else>
        <a-alert class="drawer-alert" type="info" show-icon message="V3 组合组" description="内容方向 → 手法组合 → 候选公式池；生产时最终只锁定一个标题公式和一个正文公式。" />
        <a-form-item label="内容方向" name="content_type_codes">
          <a-select v-model:value="form.content_type_codes" mode="multiple" placeholder="选择 CT01～CT07 内容方向">
            <a-select-option v-for="item in contentTypeOptions" :key="item.code" :value="item.code">{{ item.code }} · {{ item.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="创作手法（按选择顺序）" name="method_members">
          <a-select v-model:value="combinationMethodCodes" mode="multiple" :max-count="4" placeholder="选择 1～4 个手法">
            <a-select-option v-for="item in methodOptions" :key="item.code" :value="item.code">{{ item.code }} · {{ item.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="组合类型">
          <a-input :value="form.combination_type" disabled />
        </a-form-item>
        <a-form-item label="标题公式候选池" name="title_formula_candidate_codes">
          <a-select v-model:value="form.title_formula_candidate_codes" mode="multiple" placeholder="选择本组可用的标题公式">
            <a-select-option v-for="item in titleOptions" :key="item.code" :value="item.code">{{ item.code }} · {{ item.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="正文公式候选池" name="body_formula_candidate_codes">
          <a-select v-model:value="form.body_formula_candidate_codes" mode="multiple" placeholder="选择本组可用的正文公式">
            <a-select-option v-for="item in contentOptions" :key="item.code" :value="item.code">{{ item.code }} · {{ item.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="适用场景" name="scenario_description">
          <a-textarea v-model:value="form.scenario_description" :rows="3" placeholder="说明该内容方向与手法组合的适用场景" />
        </a-form-item>
        <div class="field-row">
          <a-form-item label="行业范围">
            <a-select v-model:value="form.industry_scope" mode="tags" placeholder="例如 decoration" />
          </a-form-item>
          <a-form-item label="推荐优先级">
            <a-input-number v-model:value="form.priority" :min="0" :max="10000" style="width: 100%" />
          </a-form-item>
        </div>
        <a-form-item label="推荐原因">
          <a-textarea v-model:value="form.recommendation_reason" :rows="3" placeholder="解释为什么这套组合适合该内容目标" />
        </a-form-item>
      </template>
    </a-form>

    <template #footer>
      <div class="drawer-footer">
        <a-button @click="emit('close')">取消</a-button>
        <a-button type="primary" @click="submit">确认{{ isNew ? '新增' : '修改' }}</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped lang="less">
.drawer-alert { margin-bottom: 18px; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.structure-list { display: flex; flex-direction: column; gap: 8px; }
.structure-list > div { display: grid; grid-template-columns: 24px 1fr 34px; gap: 8px; align-items: center; }
.structure-list > div > span { color: var(--color-text-secondary); text-align: center; }
.structure-list button { border: 0; background: transparent; color: var(--color-text-secondary); }
.structure-list button:not(:disabled):hover { color: var(--color-error-600); background: var(--color-error-50); }
.drawer-footer { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 640px) { .field-row { grid-template-columns: 1fr; gap: 0; } }
</style>
