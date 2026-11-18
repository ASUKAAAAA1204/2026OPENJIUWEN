<script setup lang="ts">
import type { Dim } from '../api/types'
import SegmentBar from './SegmentBar.vue'

const props = defineProps<{ dims: Dim[]; clickable?: boolean }>()
const emit = defineEmits<{ 'action:select-dim': [key: Dim['key']] }>()

const INDEX = ['①', '②', '③', '④', '⑤'] as const

/** 样本不足：n < 10，整行降饱和并在条尾标 n=6 */
function insufficient(dim: Dim): boolean {
  return dim.state === 'VALUE' && dim.den > 0 && dim.den < 10
}

function countText(dim: Dim): string {
  if (dim.state === 'NA') return '无适用项'
  if (dim.state === 'MISSING') return '未采集'
  return `${dim.num}/${dim.den}`
}

function select(dim: Dim) {
  if (!props.clickable) return
  emit('action:select-dim', dim.key)
}
</script>

<template>
  <section class="dims panel">
    <header class="dims__head">
      <h2 class="dims__title serif">五维分项</h2>
      <span class="dims__meta">
        <span class="label label--latin dims__tag">Dimensions ·</span>
        <span class="label">点击查看证据</span>
      </span>
    </header>

    <ul class="dims__list">
      <li v-for="(dim, i) in dims" :key="dim.key">
        <button
          type="button"
          class="dim"
          :class="{ 'is-insufficient': insufficient(dim), 'is-quiet': dim.state !== 'VALUE' }"
          :disabled="!clickable"
          @click="select(dim)"
        >
          <span class="dim__dot" aria-hidden="true" />
          <span class="dim__leader" aria-hidden="true" />
          <span class="dim__name">
            <span class="dim__idx">{{ INDEX[i] }}</span>
            {{ dim.label }}
          </span>
          <SegmentBar
            class="dim__bar"
            :num="dim.num"
            :den="dim.den"
            :state="dim.state"
            :badge="insufficient(dim) ? `n=${dim.den}` : undefined"
            :style="{ '--seg-delay': `${i * 60}ms` }"
          />
          <span class="dim__count mono" :class="{ 'is-quiet': dim.state !== 'VALUE' }">
            {{ countText(dim) }}
          </span>
          <span class="dim__weight label">{{ dim.weight }}%</span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.dims {
  padding: var(--sp-3) var(--sp-4) var(--sp-2);
}

.dims__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.dims__title {
  flex: none;
  font-size: var(--fs-title);
  color: var(--ink);
  white-space: nowrap;
}

.dims__meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

/* 1024 底线：左栏变窄时先去掉英文装饰标注，保留中文交互提示 */
@media (max-width: 1120px) {
  .dims__tag {
    display: none;
  }
}

.dims__list {
  margin-top: var(--sp-1);
}

.dim {
  display: grid;
  grid-template-columns: 8px 12px 64px 1fr 64px 26px;
  align-items: center;
  gap: var(--sp-1);
  width: 100%;
  height: 40px;
  text-align: left;
  color: var(--ink);
}

.dim:disabled {
  cursor: default;
}

.dim__dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--blue);
  justify-self: center;
  transition: transform var(--dur-fast) var(--ease-out);
}

/* 引线：2px 折角 + 细线，连接圆点到维度名 */
.dim__leader {
  height: 1px;
  background: var(--hairline);
  transition: background-color var(--dur-fast) var(--ease-out);
}

.dim__name {
  font-size: var(--fs-sub);
  color: var(--ink-2);
  white-space: nowrap;
  transition: color var(--dur-fast) var(--ease-out);
}

.dim__idx {
  margin-right: 4px;
  font-size: var(--fs-label);
  color: var(--ink-3);
}

.dim__count {
  font-size: var(--fs-sub);
  text-align: right;
  white-space: nowrap;
}

.dim__count.is-quiet {
  color: var(--ink-3);
  font-size: var(--fs-label-lg);
}

.dim__weight {
  text-align: right;
}

.dim:hover:not(:disabled) .dim__dot {
  transform: scale(1.15);
}

.dim:hover:not(:disabled) .dim__leader {
  background: var(--blue);
}

.dim:hover:not(:disabled) .dim__name {
  color: var(--ink);
}

.dim:hover:not(:disabled) :deep(.segbar__fill) {
  background: var(--ink);
}

/* 样本不足：整行降饱和，条尾已带 n 徽标 */
.dim.is-insufficient :deep(.segbar__fill) {
  background: var(--ink-3);
}

.dim.is-insufficient .dim__count {
  color: var(--ink-3);
}

.dim.is-quiet :deep(.segbar__fill) {
  display: none;
}

/* 720p 投影：行高 40→32，面板纵向留白收紧 */
@media (max-height: 960px) {
  .dims {
    padding: var(--sp-2) var(--sp-3) var(--sp-1);
  }

  .dims__head {
    padding-bottom: var(--sp-1);
  }

  .dim {
    height: 32px;
  }
}
</style>
