<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { PlanTier, Report } from '../api/types'
import { fmtCu, fmtScore } from '../utils/format'

const props = defineProps<{
  trend: Report['trend']
  threshold: number
  marginalFrom: number | null
  tiers: PlanTier[]
  activeCu: number | null
}>()

/** 投影紧凑（≤960px 视口）下曲线随余量生长：720p 保底 116px，最高 216px */
function chartHeight(): number {
  const vh = window.innerHeight
  if (vh > 960) return 216
  return Math.min(216, Math.max(116, vh - 704))
}

type PtGeom = {
  key: string
  x: number
  y: number
  text: string
  align: 'first' | 'mid' | 'last'
  delay: number
}

type Geom = {
  left: number
  top: number
  width: number
  height: number
  thresholdY: number
  marginalX: number | null
  tierX: number
  tierY: number | null
  hasTier: boolean
  points: PtGeom[]
}

const wrap = ref<HTMLDivElement | null>(null)
const host = ref<HTMLDivElement | null>(null)

let chart: uPlot | null = null
let observer: ResizeObserver | null = null
let raf = 0
let progress = 1
let ready = false
/** 画布颜色在挂载时从 token 读出，组件内不写色值 */
let palette = { ink: '', ink3: '' }

const empty = computed(() => props.trend.length < 2)

const last = computed(() => props.trend[props.trend.length - 1] ?? null)

const summary = computed(() => {
  const first = props.trend[0]
  const end = last.value
  if (!first || !end) return ''
  return `从 ${fmtScore(first.score)} 分到 ${fmtScore(end.score)} 分 · 花费 ${fmtCu(end.cu)} CU`
})

const ariaLabel = computed(() => {
  const end = last.value
  if (!end) return '提升曲线，暂无数据'
  return `提升曲线：横轴累计 CU，纵轴 ARS；从 ${fmtScore(
    props.trend[0].score,
  )} 分到 ${fmtScore(end.score)} 分，共 ${fmtCu(end.cu)} CU，发布门槛 ${fmtScore(
    props.threshold,
  )} 分`
})

const xMax = computed(() => (props.trend.length ? Math.max(...props.trend.map((p) => p.cu)) : 0))

/** 纵轴向外取整到 10，上下各留 5 分余量 */
const yDomain = computed<uPlot.Range.MinMax>(() => {
  if (!props.trend.length) return [0, 100]
  const scores = props.trend.map((p) => p.score)
  return [
    Math.floor((Math.min(...scores) - 5) / 10) * 10,
    Math.ceil((Math.max(...scores) + 5) / 10) * 10,
  ]
})

const activeTier = computed<PlanTier | null>(() => {
  const tiers = props.tiers
  if (!tiers.length) return null
  return tiers.find((tier) => tier.cu === props.activeCu) ?? tiers[tiers.length - 1]
})

const geom = ref<Geom>({
  left: 0,
  top: 0,
  width: 0,
  height: 0,
  thresholdY: 0,
  marginalX: null,
  tierX: 0,
  tierY: null,
  hasTier: false,
  points: [],
})

const hover = ref({ on: false, x: 0, y: 0, text: '', flip: false, above: false })

/** 读数默认落在点下方（点标注占上方），贴近底边时改到标注上方 */
const readoutStyle = computed(() => {
  const { x, y, flip, above } = hover.value
  const tx = flip ? 'calc(-100% - 8px)' : '8px'
  const ty = above ? 'calc(-100% - 26px)' : '10px'
  return { left: `${x}px`, top: `${y}px`, transform: `translate(${tx}, ${ty})` }
})

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function lowPerf(): boolean {
  return navigator.hardwareConcurrency !== undefined && navigator.hardwareConcurrency <= 4
}

function makeOpts(width: number): uPlot.Options {
  const axisFont = `10px ${token('--font-mono')}`
  palette = { ink: token('--ink'), ink3: token('--ink-3') }
  return {
    width,
    height: chartHeight(),
    padding: [10, 14, 2, 0],
    legend: { show: false },
    cursor: { show: false },
    scales: {
      x: { time: false, range: [0, xMax.value + 2] },
      y: { range: yDomain.value },
    },
    axes: [
      {
        scale: 'x',
        stroke: palette.ink3,
        font: axisFont,
        grid: { show: false },
        ticks: { show: false },
        border: { show: true, stroke: token('--hairline'), width: 1 },
        size: 22,
        gap: 6,
        splits: (_self, _idx, min, max) => {
          const out: number[] = []
          for (let v = Math.ceil(min / 10) * 10; v <= max; v += 10) out.push(v)
          return out
        },
        values: (_self, splits) => splits.map((v) => (v == null ? '' : fmtCu(v))),
      },
      {
        scale: 'y',
        stroke: palette.ink3,
        font: axisFont,
        grid: { show: false },
        ticks: { show: false },
        border: { show: true, stroke: token('--hairline'), width: 1 },
        size: 34,
        gap: 6,
        splits: (_self, _idx, min, max) => {
          const out: number[] = []
          for (let v = Math.ceil(min / 10) * 10; v <= max; v += 10) out.push(v)
          return out
        },
        values: (_self, splits) => splits.map((v) => (v == null ? '' : String(Math.round(v)))),
      },
    ],
    series: [
      { label: '累计 CU' },
      {
        label: 'ARS',
        stroke: () => 'transparent',
        width: 0,
        points: { show: false },
      },
    ],
    hooks: { draw: [drawSeries], ready: [onReady] },
  }
}

/** 折线自绘：clip 遮罩从左揭示，点按揭示进度逐个淡入 */
function drawSeries(self: uPlot) {
  if (props.trend.length < 2 || progress <= 0) return
  const ctx = self.ctx
  const ratio = ctx.canvas.width / self.width
  const box = self.bbox
  ctx.save()
  ctx.beginPath()
  ctx.rect(box.left, box.top, box.width * progress, box.height)
  ctx.clip()

  ctx.lineWidth = 1.5 * ratio
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  ctx.strokeStyle = palette.ink
  ctx.beginPath()
  props.trend.forEach((p, i) => {
    const x = self.valToPos(p.cu, 'x', true)
    const y = self.valToPos(p.score, 'y', true)
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.stroke()

  const span = Math.max(xMax.value, 1)
  props.trend.forEach((p) => {
    const alpha = Math.min(1, Math.max(0, (progress - p.cu / span) / 0.08))
    if (alpha <= 0) return
    ctx.globalAlpha = alpha
    ctx.beginPath()
    ctx.arc(
      self.valToPos(p.cu, 'x', true),
      self.valToPos(p.score, 'y', true),
      2.25 * ratio,
      0,
      Math.PI * 2,
    )
    ctx.lineWidth = 1.25 * ratio
    ctx.strokeStyle = palette.ink
    ctx.stroke()
  })

  ctx.restore()
}

/** uPlot 在构造后异步完成首次 commit，几何量必须等 ready 后再算 */
function measure() {
  const u = chart
  if (!u || !ready) return
  const ratio = u.ctx.canvas.width / u.width
  const at = (val: number, scale: 'x' | 'y') => u.valToPos(val, scale, true) / ratio
  const tier = activeTier.value
  geom.value = {
    left: u.bbox.left / ratio,
    top: u.bbox.top / ratio,
    width: u.bbox.width / ratio,
    height: u.bbox.height / ratio,
    thresholdY: at(props.threshold, 'y'),
    marginalX: props.marginalFrom === null ? null : at(props.marginalFrom, 'x'),
    tierX: tier === null ? 0 : at(tier.cu, 'x'),
    tierY: tier === null || tier.predicted === null ? null : at(tier.predicted, 'y'),
    hasTier: tier !== null,
    points: props.trend.map((p, i) => ({
      key: `${p.cu}-${p.score}`,
      x: at(p.cu, 'x'),
      y: at(p.score, 'y'),
      text: fmtScore(p.score),
      align: i === 0 ? 'first' : i === props.trend.length - 1 ? 'last' : 'mid',
      delay: 600 + i * 40,
    })),
  }
}

function onReady() {
  ready = true
  measure()
  reveal()
}

function onMove(ev: PointerEvent) {
  const u = chart
  if (!u || !ready) return
  const rect = u.over.getBoundingClientRect()
  const relX = ev.clientX - rect.left
  const idx = u.posToIdx(relX)
  const point = props.trend[idx]
  if (!point) {
    if (hover.value.on) hover.value = { ...hover.value, on: false }
    return
  }
  const ratio = u.ctx.canvas.width / u.width
  const x = u.valToPos(point.cu, 'x', true) / ratio
  const y = u.valToPos(point.score, 'y', true) / ratio
  hover.value = {
    on: true,
    x,
    y,
    text: `${fmtCu(point.cu)} CU · ${fmtScore(point.score)} 分`,
    flip: x > geom.value.width * 0.62,
    above: y > geom.value.height - 30,
  }
}

function onLeave() {
  if (hover.value.on) hover.value = { ...hover.value, on: false }
}

function reveal() {
  if (prefersReducedMotion() || lowPerf()) {
    progress = 1
    chart?.redraw()
    return
  }
  progress = 0
  const start = performance.now()
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / 600)
    progress = 1 - Math.pow(1 - t, 3)
    chart?.redraw()
    raf = t < 1 ? requestAnimationFrame(step) : 0
  }
  raf = requestAnimationFrame(step)
}

function build() {
  const el = host.value
  if (!el) return
  chart = new uPlot(
    makeOpts(Math.max(1, el.clientWidth)),
    [props.trend.map((p) => p.cu), props.trend.map((p) => p.score)],
    el,
  )
  chart.over.addEventListener('pointermove', onMove)
  chart.over.addEventListener('pointerleave', onLeave)
}

onMounted(() => {
  if (empty.value) return
  build()
  if (wrap.value) {
    observer = new ResizeObserver(() => {
      const el = host.value
      if (!el || !chart) return
      const width = Math.max(1, el.clientWidth)
      if (width === chart.width) return
      chart.setSize({ width, height: chartHeight() })
      measure()
      chart.redraw()
    })
    observer.observe(wrap.value)
  }
})

watch(
  () => [props.activeCu, props.tiers],
  () => measure(),
)

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
  observer?.disconnect()
  if (chart) {
    chart.over.removeEventListener('pointermove', onMove)
    chart.over.removeEventListener('pointerleave', onLeave)
    chart.destroy()
    chart = null
  }
})
</script>

<template>
  <section class="curve panel" aria-label="提升曲线">
    <header class="curve__head">
      <h2 class="curve__title serif">提升曲线</h2>
      <span class="curve__meta">
        <span class="label label--latin curve__tag">Curve ·</span>
        <span class="label">分 / 累计 CU</span>
      </span>
    </header>

    <p v-if="empty" class="curve__empty">暂无提升曲线 · 预测未出</p>

    <template v-else>
      <p id="curve-summary" class="curve__summary">{{ summary }}</p>

      <div ref="wrap" class="curve__wrap">
        <div ref="host" class="curve__plot" role="img" :aria-label="ariaLabel" />

        <div class="curve__overlay" aria-hidden="true">
          <div
            class="curve__threshold"
            :style="{
              left: `${geom.left}px`,
              top: `${geom.thresholdY}px`,
              width: `${geom.width}px`,
            }"
          >
            <span class="curve__threshold-label label">发布门槛 {{ fmtScore(threshold) }}</span>
          </div>

          <div
            v-if="geom.marginalX !== null"
            class="curve__marginal"
            :style="{
              left: `${geom.marginalX}px`,
              top: `${geom.top}px`,
              height: `${geom.height}px`,
            }"
          >
            <span class="curve__marginal-label">边际枯竭</span>
          </div>

          <div
            v-if="geom.hasTier"
            class="curve__tier"
            :style="{
              top: `${geom.top}px`,
              height: `${geom.height}px`,
              transform: `translateX(${geom.tierX}px)`,
            }"
          />

          <span
            v-if="geom.hasTier && geom.tierY !== null"
            class="curve__tier-dot"
            :style="{
              transform: `translate(calc(${geom.tierX}px - 50%), calc(${geom.tierY}px - 50%))`,
            }"
          />

          <span
            v-for="p in geom.points"
            :key="p.key"
            class="curve__pt mono"
            :class="`curve__pt--${p.align}`"
            :style="{
              left: `${p.x}px`,
              top: `${p.y - 6}px`,
              animationDelay: `${p.delay}ms`,
            }"
            >{{ p.text }}</span
          >

          <div v-if="hover.on" class="curve__cross">
            <span
              class="curve__cross-v"
              :style="{
                left: `${hover.x}px`,
                top: `${geom.top}px`,
                height: `${geom.height}px`,
              }"
            />
            <span
              class="curve__cross-h"
              :style="{
                left: `${geom.left}px`,
                top: `${hover.y}px`,
                width: `${geom.width}px`,
              }"
            />
            <span
              class="curve__cross-dot"
              :style="{ left: `${hover.x}px`, top: `${hover.y}px` }"
            />
            <span class="curve__cross-read mono" :style="readoutStyle">{{ hover.text }}</span>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.curve {
  padding: var(--sp-3) var(--sp-4) var(--sp-2);
}

.curve__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.curve__title {
  flex: none;
  font-size: var(--fs-title);
  color: var(--ink);
  white-space: nowrap;
}

.curve__meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

@media (max-width: 1120px) {
  .curve__tag {
    display: none;
  }
}

.curve__empty {
  padding: var(--sp-3) 0 var(--sp-2);
  font-size: var(--fs-sub);
  color: var(--ink-3);
}

.curve__summary {
  margin-top: var(--sp-2);
  font-size: var(--fs-label-lg);
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}

.curve__wrap {
  position: relative;
  margin-top: var(--sp-1);
}

.curve__plot {
  width: 100%;
}

/* 覆盖层与画布同源：坐标均从 canvas 原点换算 */
.curve__overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.curve__threshold {
  position: absolute;
  height: 0;
  border-top: 1px dashed var(--danger);
}

/* 阈值线标签放左侧：末段曲线越过门槛后，右侧留给末点读数 */
.curve__threshold-label.label {
  position: absolute;
  left: 0;
  bottom: 4px;
  color: var(--danger);
  white-space: nowrap;
}

.curve__marginal {
  position: absolute;
  width: 0;
  border-left: 1px dashed var(--ink-3);
}

/* 边际枯竭标签放竖线底部：顶部已被中间点读数占用 */
.curve__marginal-label {
  position: absolute;
  left: 4px;
  bottom: 4px;
  font-size: var(--fs-label);
  letter-spacing: var(--tracking-label);
  color: var(--ink-3);
  white-space: nowrap;
}

.curve__tier {
  position: absolute;
  left: 0;
  width: 1px;
  background: var(--blue);
  opacity: 0.55;
  transition: transform 300ms var(--ease-out);
}

.curve__tier-dot {
  position: absolute;
  left: 0;
  top: 0;
  width: 8px;
  height: 8px;
  border: 1px solid var(--blue);
  border-radius: 50%;
  background: var(--bg);
  transition: transform 300ms var(--ease-out);
}

.curve__pt {
  position: absolute;
  font-size: var(--fs-label);
  line-height: 1;
  color: var(--ink-2);
  white-space: nowrap;
  animation: pt-in 160ms var(--ease-out) both;
}

.curve__pt--first {
  transform: translate(4px, -100%);
}

.curve__pt--mid {
  transform: translate(-50%, -100%);
}

.curve__pt--last {
  transform: translate(calc(-100% - 4px), -100%);
}

@keyframes pt-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.curve__cross-v {
  position: absolute;
  width: 0;
  border-left: 1px solid var(--ink-3);
}

.curve__cross-h {
  position: absolute;
  height: 0;
  border-top: 1px solid var(--ink-3);
}

.curve__cross-dot {
  position: absolute;
  width: 7px;
  height: 7px;
  margin: -3.5px 0 0 -3.5px;
  border-radius: 50%;
  background: var(--blue);
}

.curve__cross-read {
  position: absolute;
  padding: 2px 6px;
  border: var(--hairline-w) solid var(--hairline);
  border-left: 2px solid var(--blue);
  background: var(--surface);
  font-size: var(--fs-label);
  color: var(--ink);
  white-space: nowrap;
}

@media (prefers-reduced-motion: reduce) {
  .curve__pt {
    animation: none;
  }

  .curve__tier,
  .curve__tier-dot {
    transition: none;
  }
}

/* 720p 投影：面板纵向留白收紧，图表高度由 chartHeight() 同步压缩；
   摘要仍保留在 DOM 中供读屏，视觉上让位给曲线本体 */
@media (max-height: 960px) {
  .curve {
    padding: var(--sp-2) var(--sp-3) var(--sp-1);
  }

  .curve__head {
    padding-bottom: var(--sp-1);
  }

  .curve__summary {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }

  .curve__wrap {
    margin-top: var(--sp-1);
  }
}
</style>
