<script setup lang="ts">
import { computed } from 'vue'
import type { Band, DocState, Report } from '../api/types'
import { bandCaption, bandText } from '../utils/format'
import { useTween } from '../composables/useTween'

const props = withDefaults(
  defineProps<{
    score: number | null
    band: Band | null
    counts: Report['counts']
    docState: DocState
    threshold?: number
  }>(),
  { threshold: 70 },
)

const hasScore = computed(() => props.docState === 'COMPLETE' && props.score !== null)

const { display } = useTween(() => props.score ?? 0)

const captionBand = computed(() => {
  if (hasScore.value) return bandText(props.band)
  return props.docState === 'DESIGN_ONLY' ? '未评档' : '无档位'
})

const captionText = computed(() => {
  if (hasScore.value) return bandCaption(props.band)
  return props.docState === 'DESIGN_ONLY' ? '仅设计态，不出总分' : '样本不足，不出总分'
})

/** DESIGN_ONLY 下 0 表示"未采集"，不写 0，避免被读成"评估了 0 条" */
const countsText = computed(() => {
  const unknown = props.docState === 'DESIGN_ONLY'
  const fmt = (n: number) => (unknown && n === 0 ? '—' : String(n))
  return `${fmt(props.counts.claims)} 条结论 · ${fmt(props.counts.rules)} 条规则 · ${fmt(
    props.counts.checks,
  )} 个检查`
})

const bandTone = computed(() => {
  switch (props.band) {
    case 'A':
    case 'B':
      return 'success'
    case 'C':
      return 'warning'
    case 'D':
      return 'danger'
    default:
      return 'muted'
  }
})

const ariaLabel = computed(() => {
  if (!hasScore.value) return '审计就绪度 无数据，样本不足，不可发布决策'
  const relation = (props.score as number) < props.threshold ? '低于' : '不低于'
  return `审计就绪度 ${props.score} 分，${props.band} 档，${relation}发布门槛 ${props.threshold}`
})
</script>

<template>
  <section class="score-card panel" role="group" :aria-label="ariaLabel">
    <div class="score-card__ticks" aria-hidden="true" />

    <div class="score-card__body">
      <div class="score-card__head">
        <span class="label"><span class="label--latin">Audit Readiness</span> · 审计就绪度</span>
        <span class="label score-card__threshold">发布门槛 {{ threshold }}</span>
      </div>

      <div class="score-card__figure">
        <svg class="rings" viewBox="0 0 400 400" aria-hidden="true">
          <circle class="ring ring--1" cx="200" cy="200" r="120" />
          <circle class="ring ring--2" cx="200" cy="200" r="160" />
          <circle class="ring ring--3" cx="200" cy="200" r="200" />
        </svg>

        <span v-if="hasScore" class="score-card__num serif" aria-hidden="true">{{ display }}</span>
        <span v-else class="score-card__empty" aria-hidden="true" />

        <div class="seal" :class="`seal--${bandTone}`" aria-hidden="true">
          <svg class="seal__ring" viewBox="0 0 44 44">
            <circle class="seal__circle" cx="22" cy="22" r="21" />
          </svg>
          <span class="seal__letter serif">{{ band ?? '—' }}</span>
        </div>
      </div>

      <p class="score-card__caption">
        <span class="score-card__band">{{ captionBand }}</span>
        <span class="score-card__dot">·</span>
        <span>{{ captionText }}</span>
      </p>

      <p class="score-card__counts mono">{{ countsText }}</p>
    </div>
  </section>
</template>

<style scoped>
.score-card {
  display: grid;
  grid-template-columns: 32px 1fr;
  padding: 0;
  overflow: hidden;
  min-height: 248px;
}

.score-card__ticks {
  background-image: repeating-linear-gradient(
    to bottom,
    var(--hairline) 0 1px,
    transparent 1px 16px
  );
  opacity: 0.7;
  border-right: var(--hairline-w) solid var(--hairline);
}

.score-card__body {
  padding: var(--sp-4) var(--sp-4) var(--sp-3);
  min-width: 0;
}

.score-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 2px var(--sp-2);
}

.score-card__threshold {
  color: var(--ink-3);
  white-space: nowrap;
}

.score-card__head .label {
  white-space: nowrap;
}

/* 1024 底线：左栏变窄时两项各占一行，保持左对齐而不是错位 */
@media (max-width: 1120px) {
  .score-card__head {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }
}

.score-card__figure {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: var(--sp-2);
  min-height: 68px;
}

/* 同心辅助圆：装饰层，永远在数据层之下 */
.rings {
  position: absolute;
  left: -100px;
  top: 50%;
  width: 400px;
  height: 400px;
  transform: translateY(-50%);
  pointer-events: none;
}

.ring {
  fill: none;
  stroke: var(--grid);
  stroke-width: 1;
  opacity: 0;
  animation: ring-in 400ms var(--ease-out) forwards;
}

.ring--2 {
  animation-delay: 80ms;
}

.ring--3 {
  animation-delay: 160ms;
}

@keyframes ring-in {
  to {
    opacity: 1;
  }
}

.score-card__num {
  position: relative;
  z-index: 1;
  font-size: var(--fs-score);
  line-height: 0.94;
  letter-spacing: -0.01em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

/* 无数据：灰色横线，不写 0、不写 NaN */
.score-card__empty {
  position: relative;
  z-index: 1;
  width: 56px;
  height: 2px;
  background: var(--ink-3);
}

.seal {
  position: relative;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  flex: none;
}

.seal__ring {
  position: absolute;
  inset: 0;
  transform: rotate(-90deg);
}

.seal__circle {
  fill: none;
  stroke: var(--gold);
  stroke-width: 1;
  stroke-dasharray: 132;
  stroke-dashoffset: 132;
  animation: seal-draw 320ms var(--ease-out) 120ms forwards;
}

@keyframes seal-draw {
  to {
    stroke-dashoffset: 0;
  }
}

.seal__letter {
  position: relative;
  font-size: 20px;
  line-height: 1;
}

.seal--success .seal__letter {
  color: var(--success);
}

.seal--warning .seal__letter {
  color: var(--warning);
}

.seal--danger .seal__letter {
  color: var(--danger);
}

.seal--muted .seal__letter {
  color: var(--ink-3);
}

.score-card__caption {
  margin-top: var(--sp-2);
  font-size: var(--fs-sub);
  color: var(--ink-2);
}

.score-card__band {
  color: var(--ink);
}

.score-card__dot {
  margin: 0 6px;
  color: var(--ink-3);
}

.score-card__counts {
  margin-top: var(--sp-1);
  font-size: var(--fs-label-lg);
  letter-spacing: 0.04em;
  color: var(--ink-3);
}

/* 720p 投影：去掉最小高度留白，收紧纵向 padding */
@media (max-height: 960px) {
  .score-card {
    min-height: 0;
  }

  .score-card__body {
    padding: var(--sp-1) var(--sp-3);
  }

  .score-card__figure {
    margin-top: var(--sp-1);
  }

  .score-card__caption {
    margin-top: var(--sp-1);
  }
}
</style>
