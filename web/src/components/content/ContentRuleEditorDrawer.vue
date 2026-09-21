<script setup>
import { computed, reactive, ref, watch, toRaw } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  type: { type: String, default: 'methods' },
  item: { type: Object, default: null },
  methodOptions: { type: Array, default: () => [] },
  titleOptions: { type: Array, default: () => [] },
  contentOptions: { type: Array, default: () => [] },
  contentTypeOptions: { type: Array, default: () => [] },
  defaultIndustry: { type: String, default: '' }
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
      sentence_patterns: [], tag_schema: {}, variable_schema: [], risk_rules: [], enabled: true,
      industry_scope: props.defaultIndustry ? [props.defaultIndustry] : []
    }
  }
  if (props.type === 'title_formulas') {
    return {
      code: '', name: '', suitable_scenes: [], core_goal: '', reference_examples: [],
      variable_schema: [], compatible_methods: [], risk_rules: [], enabled: true,
      industry_scope: props.defaultIndustry ? [props.defaultIndustry] : []
    }
  }
  if (props.type === 'content_formulas') {
    return {
      code: '', name: '', industry_aliases: {}, compatible_methods: [], suitable_scenes: [],
      business_pains: [], structure_schema: [''], reference_examples: [], required_variables: [],
      output_schema: {}, risk_rules: [], enabled: true,
      industry_scope: props.defaultIndustry ? [props.defaultIndustry] : []
    }
  }
  return {
    enabled: true,
    schema_version: 3,
    content_goal_codes: [],
    content_type_codes: [],
    industry_scope: props.defaultIndustry ? [props.defaultIndustry] : [],
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
    Object.assign(form, structuredClone(toRaw(props.item) || defaults()))
    form.source_content ||= {}
    form.source_content.variables ||= []
    form.source_content.emotion_lexicon ||= []
    form.source_content.method_combinations ||= []
    form.source_content.slot_schema ||= []
    if (props.type === 'combination_rules') {
      form.source_metadata ||= {}
      form.source_metadata.composition_blueprint ||= {}
      form.source_metadata.composition_blueprint.layer_sequence ||= []
      form.source_metadata.composition_blueprint.phrase_composition ||= []
      for (const field of ['conditions', 'hard_conditions']) {
        const source = form[field] || {}
        form[`_${field}_complex`] = Object.fromEntries(Object.entries(source).filter(([, value]) => value !== null && typeof value === 'object'))
        form[`_${field}_rows`] = Object.entries(source)
          .filter(([, value]) => value === null || typeof value !== 'object')
          .map(([key, value]) => ({ key, type: typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'string', value }))
      }
    }
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
const addTitleSlot = () => form.source_content.slot_schema.push({
  code: '', label: '', variable_codes: [], lexicon_codes: []
})
const removeTitleSlot = (index) => form.source_content.slot_schema.splice(index, 1)
const addLayer = () => {
  const layers = form.source_metadata.composition_blueprint.layer_sequence
  layers.push({ order: layers.length + 1, code: '', name: '' })
}
const removeLayer = (index) => {
  form.source_metadata.composition_blueprint.layer_sequence.splice(index, 1)
  form.source_metadata.composition_blueprint.layer_sequence.forEach((item, itemIndex) => { item.order = itemIndex + 1 })
}
const addPhraseRule = () => form.source_metadata.composition_blueprint.phrase_composition.push({
  layer_code: '', source: 'rule', selection: 'fixed', min_groups: 1, max_groups: 1,
  allowed_groups: [], missing_behavior: 'block'
})
const removePhraseRule = (index) => form.source_metadata.composition_blueprint.phrase_composition.splice(index, 1)
const addConstraint = (field) => form[`_${field}_rows`].push({ key: '', type: 'boolean', value: true })
const removeConstraint = (field, index) => form[`_${field}_rows`].splice(index, 1)
const changeConstraintType = (row) => {
  row.value = row.type === 'boolean' ? true : row.type === 'number' ? 0 : ''
}
const constraintObject = (rows, complex) => Object.fromEntries([
  ...Object.entries(complex || {}),
  ...rows.filter(row => row.key.trim()).map(row => [row.key.trim(), row.value])
])

const submit = async () => {
  await formRef.value?.validate()
  const value = structuredClone(toRaw(form))
  if (props.type === 'combination_rules') {
    for (const field of ['conditions', 'hard_conditions']) {
      const keys = value[`_${field}_rows`].map(row => row.key.trim()).filter(Boolean)
      if (new Set(keys).size !== keys.length || keys.some(key => key in value[`_${field}_complex`])) {
        message.error(`${field === 'conditions' ? '适用条件' : '硬约束'}的编码不能重复`)
        return
      }
    }
    value.conditions = constraintObject(value._conditions_rows, value._conditions_complex)
    value.hard_conditions = constraintObject(value._hard_conditions_rows, value._hard_conditions_complex)
    for (const key of ['_conditions_rows', '_conditions_complex', '_hard_conditions_rows', '_hard_conditions_complex']) delete value[key]
  }
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
      <template v-if="type === 'title_formulas' || type === 'content_formulas'">
        <details v-if="form.source_content.cross_industry" class="drawer-alert">
          <summary>通用应用说明（其他行业共用，可编辑）</summary>
          <a-form-item label="通用公式名称"><a-input v-model:value="form.source_content.cross_industry.name" /></a-form-item>
          <a-form-item label="通用核心目标"><a-textarea v-model:value="form.source_content.cross_industry.core_goal" :rows="3" /></a-form-item>
          <a-form-item v-if="type === 'content_formulas'" label="通用正文结构（每行一段）"><a-textarea :value="form.source_content.cross_industry.structure_schema.join('\n')" :rows="8" @update:value="form.source_content.cross_industry.structure_schema = $event.split('\n').filter(Boolean)" /></a-form-item>
        </details>
        <a-form-item v-if="type === 'content_formulas'" label="核心目标（原文）"><a-textarea v-model:value="form.source_content.core_goal" :rows="3" /></a-form-item>
        <a-form-item label="变量说明（原文）"><a-textarea :value="form.source_content.variables.join('\n')" :rows="4" @update:value="form.source_content.variables = $event.split('\n').filter(Boolean)" /></a-form-item>
        <a-form-item v-if="type === 'title_formulas'" label="情绪词库"><a-select v-model:value="form.source_content.emotion_lexicon" mode="tags" /></a-form-item>
        <a-form-item v-if="type === 'content_formulas'" label="适配创作手法（原文）"><a-select v-model:value="form.source_content.method_combinations" mode="tags" /></a-form-item>
      </template>
      <a-form-item v-if="type === 'combination_rules'" label="启用状态"><a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item>
      <template v-if="type !== 'combination_rules'">
        <div class="field-row">
          <a-form-item label="规则编码" name="code">
            <a-input v-model:value="form.code" :disabled="!isNew" :placeholder="`${typeMeta.codePrefix}01`" />
          </a-form-item>
          <a-form-item label="名称" name="name">
            <a-input v-model:value="form.name" placeholder="让运营人员一眼识别用途" />
          </a-form-item>
        </div>
        <a-form-item label="行业范围">
          <a-select v-model:value="form.industry_scope" mode="tags" placeholder="留空表示全部行业；例如 decoration" />
        </a-form-item>
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
        <a-form-item label="标题槽位">
          <div class="blueprint-list">
            <div v-for="(slot, index) in form.source_content.slot_schema" :key="index" class="title-slot-card">
              <div class="field-row">
                <a-input v-model:value="slot.code" placeholder="槽位编码，例如 area_or_house_type" />
                <a-input v-model:value="slot.label" placeholder="显示名称，例如 面积/房型" />
              </div>
              <a-select v-model:value="slot.variable_codes" mode="tags" placeholder="可选事实变量；同一行按 one-of 选择" />
              <a-select v-model:value="slot.lexicon_codes" mode="tags" placeholder="可选标题词库；同一行按 one-of 选择" />
              <button type="button" class="lucide-icon-btn phrase-remove" @click="removeTitleSlot(index)"><Trash2 :size="16" />删除本槽位</button>
            </div>
            <a-button block @click="addTitleSlot"><Plus :size="15" />添加标题槽位</a-button>
          </div>
          <p class="constraint-note">每一行是必填槽位；同一行中的变量和词库为“任选一个”，用于表达“面积/房型”这类公式。</p>
        </a-form-item>
        <a-form-item label="参考示例">
          <a-textarea :value="(form.reference_examples || []).join('\n\n')" :rows="4" placeholder="每个案例用空行分隔" @update:value="form.reference_examples = $event.split(/\n\s*\n/).filter(Boolean)" />
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
          <a-textarea :value="(form.reference_examples || []).join('\n\n')" :rows="8" placeholder="每个案例用空行分隔" @update:value="form.reference_examples = $event.split(/\n\s*\n/).filter(Boolean)" />
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
          <a-input :value="{ single: '单手法', double: '双手法', triple: '三手法', quadruple: '四手法' }[form.combination_type]" disabled />
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
          <a-form-item label="必需业务变量">
            <a-select v-model:value="form.required_variable_codes" mode="tags" placeholder="例如 city、area、price" />
          </a-form-item>
          <a-form-item label="必需 Evidence 类型">
            <a-select v-model:value="form.required_evidence_types" mode="tags" placeholder="例如 project_quote" />
          </a-form-item>
        </div>
        <a-form-item label="适用条件">
          <div class="blueprint-list">
            <div v-for="(row, index) in form._conditions_rows" :key="index" class="constraint-row">
              <a-input v-model:value="row.key" placeholder="条件编码，例如 price_available" />
              <a-select v-model:value="row.type" @change="changeConstraintType(row)"><a-select-option value="boolean">是/否</a-select-option><a-select-option value="number">数字</a-select-option><a-select-option value="string">文本</a-select-option></a-select>
              <a-switch v-if="row.type === 'boolean'" v-model:checked="row.value" />
              <a-input-number v-else-if="row.type === 'number'" v-model:value="row.value" style="width: 100%" />
              <a-input v-else v-model:value="row.value" />
              <button type="button" class="lucide-icon-btn" @click="removeConstraint('conditions', index)"><Trash2 :size="16" /></button>
            </div>
            <p v-if="Object.keys(form._conditions_complex || {}).length" class="constraint-note">复合条件由系统保留，本表单只维护布尔、数字和文本条件。</p>
            <a-button block @click="addConstraint('conditions')"><Plus :size="15" />添加适用条件</a-button>
          </div>
        </a-form-item>
        <a-form-item label="硬约束">
          <div class="blueprint-list">
            <div v-for="(row, index) in form._hard_conditions_rows" :key="index" class="constraint-row">
              <a-input v-model:value="row.key" placeholder="约束编码，例如 unsupported_numbers" />
              <a-select v-model:value="row.type" @change="changeConstraintType(row)"><a-select-option value="boolean">是/否</a-select-option><a-select-option value="number">数字</a-select-option><a-select-option value="string">文本</a-select-option></a-select>
              <a-switch v-if="row.type === 'boolean'" v-model:checked="row.value" />
              <a-input-number v-else-if="row.type === 'number'" v-model:value="row.value" style="width: 100%" />
              <a-input v-else v-model:value="row.value" />
              <button type="button" class="lucide-icon-btn" @click="removeConstraint('hard_conditions', index)"><Trash2 :size="16" /></button>
            </div>
            <p v-if="Object.keys(form._hard_conditions_complex || {}).length" class="constraint-note">公式对等复合硬约束由系统保留，保存不会覆盖。</p>
            <a-button block @click="addConstraint('hard_conditions')"><Plus :size="15" />添加硬约束</a-button>
          </div>
        </a-form-item>
        <a-form-item label="层级顺序">
          <div class="blueprint-list">
            <div v-for="(layer, index) in form.source_metadata.composition_blueprint.layer_sequence" :key="index" class="blueprint-layer-row">
              <a-input v-model:value="layer.code" placeholder="层级编码" />
              <a-input v-model:value="layer.name" placeholder="层级名称" />
              <button type="button" class="lucide-icon-btn" @click="removeLayer(index)"><Trash2 :size="16" /></button>
            </div>
            <a-button block @click="addLayer"><Plus :size="15" />添加层级</a-button>
          </div>
        </a-form-item>
        <a-form-item label="词组组合">
          <div class="blueprint-list">
            <div v-for="(phraseRule, index) in form.source_metadata.composition_blueprint.phrase_composition" :key="index" class="phrase-rule-card">
              <div class="field-row">
                <a-input v-model:value="phraseRule.layer_code" placeholder="层级编码" />
                <a-input v-model:value="phraseRule.source" placeholder="数据来源，例如 rule" />
              </div>
              <div class="phrase-rule-grid">
                <a-select v-model:value="phraseRule.selection" placeholder="选择方式">
                  <a-select-option value="fixed">fixed</a-select-option>
                  <a-select-option value="all">all</a-select-option>
                  <a-select-option value="random">random</a-select-option>
                  <a-select-option value="available">available</a-select-option>
                </a-select>
                <a-input-number v-model:value="phraseRule.min_groups" :min="0" placeholder="最少组数" />
                <a-input-number v-model:value="phraseRule.max_groups" :min="0" placeholder="最多组数" />
                <a-select v-model:value="phraseRule.missing_behavior" placeholder="缺失处理">
                  <a-select-option value="block">block</a-select-option>
                  <a-select-option value="omit">omit</a-select-option>
                  <a-select-option value="ask_user">ask_user</a-select-option>
                </a-select>
              </div>
              <a-select v-model:value="phraseRule.allowed_groups" mode="tags" placeholder="允许词组，输入后回车" />
              <button type="button" class="lucide-icon-btn phrase-remove" @click="removePhraseRule(index)"><Trash2 :size="16" />删除本条</button>
            </div>
            <a-button block @click="addPhraseRule"><Plus :size="15" />添加词组规则</a-button>
          </div>
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
.blueprint-list { display: flex; flex-direction: column; gap: 10px; }
.blueprint-layer-row { display: grid; grid-template-columns: 1fr 1fr 34px; gap: 8px; align-items: center; }
.blueprint-list button { border: 0; background: transparent; color: var(--color-text-secondary); }
.phrase-rule-card { position: relative; display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--color-border-secondary); border-radius: 8px; }
.title-slot-card { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--color-border-secondary); border-radius: 8px; }
.phrase-rule-grid { display: grid; grid-template-columns: 1.2fr 1fr 1fr 1.2fr; gap: 8px; }
.constraint-row { display: grid; grid-template-columns: 1.5fr 100px 1fr 34px; gap: 8px; align-items: center; }
.constraint-note { margin: 0; color: var(--gray-600); font-size: 12px; }
.phrase-remove { align-self: flex-end; display: inline-flex; gap: 4px; align-items: center; }
.drawer-footer { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 640px) { .field-row, .phrase-rule-grid { grid-template-columns: 1fr; gap: 8px; } }
</style>
