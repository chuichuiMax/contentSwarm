<script setup>
import { Check } from 'lucide-vue-next'

const props = defineProps({
  current: { type: Number, default: 1 },
  completedThrough: { type: Number, default: null }
})

defineEmits(['select'])

const steps = [
  { index: 1, label: '业务素材', description: '形成统一简报' },
  { index: 2, label: '内容发布', description: '人工选择标题' },
  { index: 3, label: '审核交付', description: '编辑、审核与版本' }
]

const isCompleted = (index) =>
  (props.completedThrough ?? props.current - 1) >= index
</script>

<template>
  <ol class="content-stage-stepper" aria-label="内容生产阶段">
    <li
      v-for="step in steps"
      :key="step.index"
      :class="{ active: current === step.index, completed: isCompleted(step.index) }"
    >
      <button
        type="button"
        :disabled="step.index > current"
        @click="$emit('select', step.index)"
      >
        <span class="step-index">
          <Check v-if="isCompleted(step.index)" :size="24" stroke-width="3" />
          <template v-else>{{ step.index }}</template>
        </span>
        <span class="step-copy">
          <strong>{{ step.label }}</strong>
          <small>{{ step.description }}</small>
        </span>
      </button>
    </li>
  </ol>
</template>

<style scoped lang="less">
.content-stage-stepper {
  display: flex;
  align-items: center;
  list-style: none;
  margin: 0;
  padding: 10px 0;

  li {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;

    &:not(:last-child)::after {
      content: '';
      min-width: 24px;
      flex: 1;
      height: 1px;
      margin: 0 20px;
      background: var(--gray-200);
    }
  }

  button {
    min-width: 0;
    flex: 0 1 auto;
    min-height: 56px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 4px 0;
    border: 0;
    background: transparent;
    color: var(--color-text-secondary);
    text-align: left;
    cursor: pointer;

    &:disabled {
      cursor: default;
    }

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 4px;
      border-radius: 4px;
    }
  }

  .active button { color: var(--main-700); }

  .completed button {
    color: var(--color-text);
  }

  .step-index {
    width: 42px;
    height: 42px;
    flex: 0 0 42px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--gray-200);
    border-radius: 50%;
    font-weight: 600;
  }

  .active .step-index {
    border-color: var(--main-color);
    background: var(--main-color);
    color: var(--gray-0);
  }

  .completed .step-index {
    border-color: var(--color-success-700);
    background: var(--color-success-700);
    color: var(--gray-0);
  }

  .step-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;

    strong {
      color: var(--color-text);
      font-size: 17px;
      font-weight: 600;
      white-space: nowrap;
    }

    small {
      color: var(--color-text-tertiary);
      font-size: 13px;
      white-space: nowrap;
    }
  }
}

@media (max-width: 800px) {
  .content-stage-stepper {
    padding: 6px 0;

    li:not(:last-child)::after {
      min-width: 8px;
      margin: 0 8px;
    }

    button {
      min-height: 52px;
      gap: 8px;
    }

    .step-copy small {
      display: none;
    }

    .step-copy strong {
      font-size: 13px;
    }

    .step-index {
      width: 34px;
      height: 34px;
      flex-basis: 34px;
      font-size: 12px;

      svg {
        width: 20px;
        height: 20px;
      }
    }
  }
}

@media (max-width: 520px) {
  .content-stage-stepper {
    li:not(:last-child)::after { display: none; }
    button { justify-content: center; }
    .step-copy strong { font-size: 12px; }
  }
}
</style>
