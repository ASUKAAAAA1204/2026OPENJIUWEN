<script setup lang="ts">
import { computed } from 'vue'
import type { Plan } from '../api/types'
import { bandText, fmtCu, fmtGain, fmtRatio, fmtScore } from '../utils/format'

const props = defineProps<{ plan: Plan }>()

type Row = Plan['actions'][number] & { ratio: number | null }

/** 严格按性价比降序；cu<=0 视为不可比，排在末尾（比值只在排序内部用） */
const rows = computed<Row[]>(() =>
  props.plan.actions
    .map((action) => ({
      ...action,
      ratio:
        action.cu > 0 && Number.isFinite(action.gain) ? action.gain / action.cu : null,
    }))
    .sort((a, b) => (b.ratio ?? -Infinity) - (a.ratio ?? -Infinity)),
)

const maxRatio = computed(() =>
  rows.value.reduce((max, row) => Math.max(max, row.ratio ?? 0), 0),
)

/** 微条长度只做比例映射，不改数字 */
function barWidth(row: Row): string {
  if (row.ratio === null || row.ratio <= 0 || maxRatio.value <= 0) return '0'
  return `${(row.ratio / maxRatio.value) * 100}%`
}

const hasPrediction = computed(
  () => props.plan.predicted !== null && Number.isFinite(props.plan.predicted),
)
</script>

<template>
  <section class="plan panel" aria-label="改造方案，按性价比降序">
    <header class="plan__head">
      <h2 class="plan__title serif">改造方案</h2>
      <span class="plan__meta">
        <span class="label label--latin plan__tag">Plan ·</span>
        <span class="label">按性价比降序 · {{ rows.length }} 项</span>
      </span>
    </header>

    <p v-if="rows.length === 0" class="plan__empty">暂无方案 · 样本不足时不出报价</p>

    <table v-else class="plan__table">
      <thead>
        <tr>
          <th scope="col" class="plan__th plan__th--act label">动作</th>
          <th scope="col" class="plan__th plan__th--num label">数量</th>
          <th scope="col" class="plan__th plan__th--num label">成本 CU</th>
          <th scope="col" class="plan__th plan__th--num label">预期增益</th>
          <th scope="col" class="plan__th plan__th--ratio label">性价比（分/CU）</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, i) in rows"
          :key="`${row.act}-${i}`"
          class="plan__row"
          :style="{ '--row-delay': `${i * 40}ms` }"
        >
          <td class="plan__cell plan__act">{{ row.label }}</td>
          <td class="plan__cell plan__num mono">{{ row.n }}</td>
          <td class="plan__cell plan__num mono">{{ fmtCu(row.cu) }}</td>
          <td class="plan__cell plan__num mono">{{ fmtGain(row.gain) }}</td>
          <td class="plan__cell plan__ratio-cell">
            <span class="plan__ratio mono">{{ fmtRatio(row.gain, row.cu) }}</span>
            <span v-if="row.ratio !== null && row.ratio > 0" class="plan__bar" aria-hidden="true">
              <span class="plan__bar-fill" :style="{ width: barWidth(row) }" />
            </span>
          </td>
        </tr>
      </tbody>
    </table>

    <footer v-if="rows.length > 0" class="plan__total">
      <span class="label plan__total-label">合计</span>
      <span class="plan__total-cu mono">{{ fmtCu(plan.budget_cu) }} CU</span>
      <span class="plan__arrow" aria-hidden="true">→</span>
      <template v-if="hasPrediction">
        <span class="plan__predicted serif">{{ fmtScore(plan.predicted) }}</span>
        <span class="plan__predicted-unit">分（{{ bandText(plan.target_band) }}）</span>
      </template>
      <span v-else class="plan__pending">
        目标 {{ plan.target_band }} 档 · 预测未出
      </span>
      <span class="label plan__buffer">含 {{ plan.cu_buffer_pct }}% 缓冲</span>
    </footer>
  </section>
</template>

<style scoped>
.plan {
  padding: var(--sp-3) var(--sp-4) var(--sp-2);
}

.plan__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.plan__title {
  flex: none;
  font-size: var(--fs-title);
  color: var(--ink);
  white-space: nowrap;
}

.plan__meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

@media (max-width: 1120px) {
  .plan__tag {
    display: none;
  }
}

.plan__empty {
  padding: var(--sp-3) 0 var(--sp-2);
  font-size: var(--fs-sub);
  color: var(--ink-3);
}

.plan__table {
  margin-top: var(--sp-1);
}

.plan__th {
  padding: 0 0 6px;
  text-align: left;
  font-weight: 500;
  white-space: nowrap;
}

.plan__th--num,
.plan__th--ratio {
  text-align: right;
}

.plan__th--num {
  width: 72px;
  padding-left: var(--sp-2);
}

.plan__th--ratio {
  width: 104px;
  padding-left: var(--sp-2);
}

.plan__row {
  height: 40px;
  border-bottom: var(--hairline-w) solid var(--hairline);
  animation: row-in 180ms var(--ease-out) both;
  animation-delay: var(--row-delay);
}

.plan__row:last-child {
  border-bottom: 0;
}

.plan__cell {
  padding: 0;
  vertical-align: middle;
  font-size: var(--fs-sub);
  white-space: nowrap;
}

.plan__act {
  color: var(--ink);
}

.plan__num {
  text-align: right;
  color: var(--ink-2);
  padding-left: var(--sp-2);
}

.plan__ratio-cell {
  text-align: right;
  padding-left: var(--sp-2);
}

.plan__ratio {
  color: var(--ink);
}

.plan__bar {
  display: block;
  width: 64px;
  height: 3px;
  margin: 3px 0 0 auto;
  background: var(--hairline);
  overflow: hidden;
}

.plan__bar-fill {
  display: block;
  height: 100%;
  background: var(--blue);
  transform-origin: left center;
  animation: bar-grow 240ms var(--ease-out) both;
  animation-delay: var(--row-delay);
  transition: width var(--dur-slow) var(--ease-out);
}

@keyframes row-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes bar-grow {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(1);
  }
}

.plan__total {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: var(--hairline-w) solid var(--hairline);
}

.plan__total-label {
  margin-right: 2px;
}

.plan__total-cu {
  font-size: var(--fs-title);
  color: var(--ink);
}

.plan__arrow {
  color: var(--ink-3);
}

.plan__predicted {
  font-size: 24px;
  line-height: 1;
  color: var(--ink);
}

.plan__predicted-unit {
  font-size: var(--fs-sub);
  color: var(--ink-2);
  white-space: nowrap;
}

.plan__pending {
  font-size: var(--fs-sub);
  color: var(--ink-3);
  white-space: nowrap;
}

.plan__buffer {
  margin-left: auto;
  white-space: nowrap;
}

/* 720p 投影：行高 40→32，面板纵向留白收紧 */
@media (max-height: 960px) {
  .plan {
    padding: var(--sp-2) var(--sp-3) var(--sp-1);
  }

  .plan__head {
    padding-bottom: var(--sp-1);
  }

  .plan__row {
    height: 28px;
  }

  .plan__th {
    padding-bottom: 4px;
  }

  .plan__total {
    margin-top: var(--sp-1);
    padding-top: var(--sp-1);
  }

  .plan__predicted {
    font-size: 20px;
  }
}
</style>
