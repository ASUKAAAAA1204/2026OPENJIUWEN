<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { Band, DocState, GateDecision, Plan } from '../api/types'
import { fmtCu, fmtScore } from '../utils/format'
import { ackReasonLabel, type AckRecord } from '../utils/ack'
import AckForm from './AckForm.vue'

const props = defineProps<{
  open: boolean
  decision: GateDecision
  reasonCode?: string
  score: number | null
  band: Band | null
  threshold: number
  docState: DocState
  plan: Plan
  record: AckRecord | null
}>()

const emit = defineEmits<{
  'action:repair': []
  'action:release': []
  'action:ack': [record: AckRecord]
  'action:close': []
}>()

/** 关闭原因码 → 人话；未登记的原因码原样显示，不猜 */
const GATE_REASON_TEXT: Record<string, string> = {
  ARS_BELOW_THRESHOLD: '总审计就绪度低于发布门槛',
  SAMPLE_INSUFFICIENT: '样本量不足，未出总分',
  DESIGN_ACK_REQUIRED: '仅设计态结论，需显式放行',
}

const panelRef = ref<HTMLElement | null>(null)
const primaryRef = ref<HTMLElement | null>(null)
const keepRef = ref<HTMLElement | null>(null)

/** decision = 决策区；ack = 豁免表单；record = 已豁免留痕 */
const view = ref<'decision' | 'ack' | 'record'>('decision')
const confirming = ref(false)
let restoreFocus: HTMLElement | null = null

const isBlock = computed(() => props.decision === 'BLOCK')
const designOnly = computed(() => props.docState === 'DESIGN_ONLY')
const canRepair = computed(
  () => isBlock.value && props.docState === 'COMPLETE' && props.plan.predicted !== null,
)

/** 样本不足时无档位可接受，豁免也不可用 */
const canAck = computed(() => props.docState === 'COMPLETE')

const title = computed(() => {
  if (props.record) return '已豁免发布'
  if (view.value === 'ack') return '豁免发布'
  if (isBlock.value) return '阻断发布'
  return designOnly.value ? '设计态放行' : '放行发布'
})

const scoreLine = computed(() => {
  if (props.score === null) {
    return props.docState === 'DESIGN_ONLY' ? '仅设计态，未出总分' : '样本不足，未出总分'
  }
  return `当前 ${fmtScore(props.score)} 分${props.band ? ` · ${props.band} 档` : ''}`
})

const metaText = computed(() => {
  if (props.record) return `原因码 ${props.record.code} · ${props.record.date}`
  return `${scoreLine.value} · 门槛 ${fmtScore(props.threshold)} 分`
})

const reasonText = computed(() => {
  if (!props.reasonCode) return isBlock.value ? '未登记闸门原因' : '已达发布门槛'
  return GATE_REASON_TEXT[props.reasonCode] ?? '未登记闸门原因'
})

const sealTone = computed(() => {
  if (isBlock.value) return 'danger'
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

const quoteText = computed(() => {
  if (!canRepair.value) return '无法出具修复报价'
  const p = props.plan
  return `${fmtCu(p.budget_cu)} CU 报价\n实际 ${fmtCu(p.total_cu)} CU · 含 ${p.cu_buffer_pct}% 缓冲`
})

const repairLabel = computed(() =>
  canRepair.value ? `执行修复 → ${fmtScore(props.plan.predicted)} 分` : '修复不可用',
)

const repairDesc = computed(() => {
  if (canRepair.value) return '按完整方案重算后重新判定闸门'
  return canAck.value ? '当前状态无法出具修复报价，可走豁免留痕' : '样本不足，先补齐样本再发起发布'
})

const ackDesc = computed(() =>
  canAck.value ? '选择原因码并填写备注，留痕后发布' : '无档位可接受，豁免不可用',
)

const passDesc = computed(() =>
  designOnly.value
    ? '仅评估可编译（C）分项；未达标只提示，不阻断运行态评估。'
    : '当前结论达到发布门槛，可直接签发。',
)

const releaseLabel = computed(() => (designOnly.value ? '记录设计态放行' : '放行发布'))

const ariaLabel = computed(() => `${title.value}，${reasonText.value}，${metaText.value}`)

function focusPrimary() {
  if (!props.open) return
  void nextTick(() => (primaryRef.value ?? panelRef.value)?.focus())
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      view.value = props.record ? 'record' : 'decision'
      confirming.value = false
      restoreFocus = document.activeElement as HTMLElement | null
      document.body.style.overflow = 'hidden'
      await nextTick()
      focusPrimary()
    } else {
      document.body.style.overflow = ''
      restoreFocus?.focus?.()
      restoreFocus = null
    }
  },
  { immediate: true },
)

/** 从豁免表单返回决策区时，焦点回到主按钮 */
watch(view, (next) => {
  if (next === 'decision') focusPrimary()
})

/** 修复后闸门从 BLOCK 切到 PASS：回到决策区并重新聚焦主按钮 */
watch(
  () => props.decision,
  () => {
    view.value = props.record ? 'record' : 'decision'
    confirming.value = false
    focusPrimary()
  },
)

function onKeydown(e: KeyboardEvent) {
  // 策划书 §5.2：Esc 不关闭，必须走二次确认
  if (e.key === 'Escape') {
    e.preventDefault()
    return
  }
  if (e.key !== 'Tab' || !panelRef.value) return
  const items = Array.from(
    panelRef.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  )
  const active = document.activeElement
  if (items.length === 0) {
    e.preventDefault()
    panelRef.value.focus()
    return
  }
  const first = items[0]
  const last = items[items.length - 1]
  if (e.shiftKey) {
    if (active === first || !panelRef.value.contains(active)) {
      e.preventDefault()
      last.focus()
    }
  } else if (active === last || !panelRef.value.contains(active)) {
    e.preventDefault()
    first.focus()
  }
}

function askClose() {
  confirming.value = true
  void nextTick(() => keepRef.value?.focus())
}

function closeDirect() {
  emit('action:close')
}

function onAckSubmit(record: AckRecord) {
  emit('action:ack', record)
}

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="gate">
      <div v-if="open" class="gate">
        <div class="gate__scrim" />

        <div
          ref="panelRef"
          class="gate__panel panel"
          role="dialog"
          aria-modal="true"
          :aria-label="ariaLabel"
          tabindex="-1"
          @keydown="onKeydown"
        >
          <span class="gate__corner gate__corner--tr" aria-hidden="true" />
          <span class="gate__corner gate__corner--bl" aria-hidden="true" />
          <div v-if="isBlock" class="gate__rule" aria-hidden="true" />

          <div class="gate__seal" :class="`is-${sealTone}`" aria-hidden="true">
            <svg viewBox="0 0 56 56">
              <circle cx="28" cy="28" r="27" />
            </svg>
            <span class="gate__seal-letter serif">{{ band ?? '—' }}</span>
          </div>

          <header class="gate__head">
            <p class="label">Gate · 发布闸</p>
            <h2 class="gate__title serif">{{ title }}</h2>
            <p class="gate__meta mono">{{ metaText }}</p>
            <p v-if="!record" class="gate__reason">
              <span class="label">原因</span>
              <span class="gate__reason-text">{{ reasonText }}</span>
              <span v-if="reasonCode" class="gate__code mono">{{ reasonCode }}</span>
            </p>
          </header>

          <div class="gate__body">
            <div v-if="record" class="gate__record">
              <dl class="gate__dl">
                <dt class="label">原因码</dt>
                <dd>
                  <span class="mono">{{ record.code }}</span>
                  <span class="gate__dl-sub"> · {{ ackReasonLabel(record.code) }}</span>
                </dd>
                <dt class="label">备注</dt>
                <dd>{{ record.note }}</dd>
                <dt class="label">日期</dt>
                <dd class="mono">{{ record.date }}</dd>
              </dl>
              <p class="gate__record-note">
                本次发布未达门槛 {{ fmtScore(threshold) }} 分，已按豁免留痕。
              </p>
              <button ref="primaryRef" type="button" class="btn btn--primary" @click="emit('action:close')">
                返回面板
              </button>
            </div>

            <AckForm
              v-else-if="view === 'ack'"
              @action:submit="onAckSubmit"
              @action:back="view = 'decision'"
            />

            <div v-else-if="isBlock" class="gate__decisions">
              <div class="gate__decision">
                <p class="label">方案 A · 修复</p>
                <div class="gate__figure-row">
                  <span v-if="canRepair" class="gate__figure serif">{{ fmtScore(plan.predicted) }}</span>
                  <span v-else class="gate__figure gate__figure--empty">—</span>
                  <span class="gate__unit">
                    {{ canRepair ? `分 · ${plan.target_band} 档` : '无法出分' }}
                  </span>
                </div>
                <p class="gate__quote mono">{{ quoteText }}</p>
                <p class="gate__desc">{{ repairDesc }}</p>
                <button
                  ref="primaryRef"
                  type="button"
                  class="btn btn--primary gate__btn"
                  :disabled="!canRepair"
                  @click="emit('action:repair')"
                >
                  {{ repairLabel }}
                </button>
              </div>

              <div class="gate__decision">
                <p class="label">方案 B · 豁免</p>
                <div class="gate__figure-row">
                  <span class="gate__figure gate__figure--empty">{{ fmtScore(score) }}</span>
                  <span class="gate__unit">{{ score !== null ? '分 · 维持原分' : '未出总分' }}</span>
                </div>
                <p class="gate__quote mono">原因码 · 备注 · 日期</p>
                <p class="gate__desc">{{ ackDesc }}</p>
                <button
                  type="button"
                  class="btn gate__btn gate__btn--ack"
                  :disabled="!canAck"
                  @click="view = 'ack'"
                >
                  {{ canAck ? '豁免发布' : '豁免不可用' }}
                </button>
              </div>
            </div>

            <div v-else class="gate__single">
              <p class="label">结论</p>
              <div class="gate__figure-row">
                <span v-if="score !== null" class="gate__figure serif">{{ fmtScore(score) }}</span>
                <span v-else class="gate__figure gate__figure--empty">—</span>
                <span class="gate__unit">
                  {{ score !== null && band ? `分 · ${band} 档` : '未出总分' }}
                </span>
              </div>
              <p class="gate__desc">{{ passDesc }}</p>
              <button ref="primaryRef" type="button" class="btn btn--primary gate__btn" @click="emit('action:release')">
                {{ releaseLabel }}
              </button>
            </div>
          </div>

          <footer v-if="view === 'decision' && !record" class="gate__foot">
            <div v-if="confirming" class="gate__confirm">
              <p class="gate__confirm-text">离开将不记录本次决策</p>
              <button ref="keepRef" type="button" class="btn btn--primary" @click="confirming = false">
                留下继续
              </button>
              <button type="button" class="btn" @click="closeDirect">离开并暂缓</button>
            </div>
            <div v-else class="gate__foot-row">
              <button type="button" class="gate__quiet" @click="askClose">稍后处理</button>
              <span class="gate__foot-note mono">决策将写入 ars 记录</span>
            </div>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.gate {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: grid;
  place-items: center;
  padding: var(--sp-3);
}

/* 遮罩：背景模糊 0→12px，不用实心色块压住下方数据 */
.gate__scrim {
  position: absolute;
  inset: 0;
  background: var(--scrim);
  backdrop-filter: blur(var(--blur-panel));
}

.gate__panel {
  position: relative;
  width: 520px;
  max-width: calc(100vw - var(--sp-6));
  max-height: calc(100vh - var(--sp-6));
  padding: var(--sp-3) var(--sp-4) var(--sp-2);
  overflow-y: auto;
  outline: none;
}

/* 四角 L 标：基础层已给左上/右下，这里补右上/左下 */
.gate__corner {
  position: absolute;
  width: 12px;
  height: 12px;
  pointer-events: none;
}

.gate__corner--tr {
  top: 0;
  right: 0;
  border-top: var(--hairline-w) solid var(--hairline);
  border-right: var(--hairline-w) solid var(--hairline);
}

.gate__corner--bl {
  left: 0;
  bottom: 0;
  border-bottom: var(--hairline-w) solid var(--hairline);
  border-left: var(--hairline-w) solid var(--hairline);
}

/* 阻断态：左缘 2px 红线自上而下画出（180ms，延迟 80ms） */
.gate__rule {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 2px;
  background: var(--danger);
  transform-origin: top;
  animation: gate-rule 180ms var(--ease-out) 80ms both;
}

@keyframes gate-rule {
  from {
    transform: scaleY(0);
  }
  to {
    transform: scaleY(1);
  }
}

/* 印章：scale 1.5→1 + 旋转 -6°→0 + 淡入，仅一次（220ms spring） */
.gate__seal {
  position: absolute;
  top: var(--sp-3);
  right: var(--sp-4);
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  animation: gate-seal 220ms var(--ease-spring) 120ms both;
}

@keyframes gate-seal {
  from {
    opacity: 0;
    transform: scale(1.5) rotate(-6deg);
  }
  to {
    opacity: 1;
    transform: scale(1) rotate(0deg);
  }
}

.gate__seal svg {
  position: absolute;
  inset: 0;
  fill: none;
  stroke: currentColor;
  stroke-width: 1;
}

.gate__seal.is-danger {
  color: var(--danger);
}

.gate__seal.is-success {
  color: var(--success);
}

.gate__seal.is-warning {
  color: var(--warning);
}

.gate__seal.is-muted {
  color: var(--ink-3);
}

.gate__seal-letter {
  position: relative;
  font-size: 24px;
  line-height: 1;
}

.gate__head {
  padding-right: 72px;
}

.gate__title {
  margin-top: 4px;
  font-size: 24px;
  line-height: 1.2;
  color: var(--ink);
}

.gate__meta {
  margin-top: 4px;
  font-size: var(--fs-label-lg);
  letter-spacing: 0.04em;
  color: var(--ink-2);
}

.gate__reason {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px var(--sp-1);
  margin-top: var(--sp-1);
  font-size: var(--fs-sub);
  color: var(--ink);
}

.gate__code {
  font-size: var(--fs-label);
  letter-spacing: 0.04em;
  color: var(--ink-3);
}

.gate__body {
  margin-top: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: var(--hairline-w) solid var(--hairline);
}

/* 两张决策区各 48%，中间 1px hairline，不做填充卡片 */
.gate__decisions {
  position: relative;
  display: grid;
  grid-template-columns: 48% 48%;
  justify-content: space-between;
}

.gate__decisions::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  border-left: var(--hairline-w) solid var(--hairline);
}

.gate__decision {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  min-width: 0;
}

.gate__figure-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-height: 34px;
}

.gate__figure {
  font-size: 32px;
  line-height: 1;
  color: var(--ink);
}

.gate__figure--empty {
  color: var(--ink-3);
}

.gate__unit {
  font-size: var(--fs-label-lg);
  color: var(--ink-2);
}

.gate__quote {
  font-size: var(--fs-label);
  line-height: 1.6;
  letter-spacing: 0.02em;
  color: var(--ink-3);

  /* 报价分两行（报价 / 实际），窄栏不再从「实际」中间折行 */
  white-space: pre-line;
}

.gate__desc {
  min-height: 34px;
  font-size: var(--fs-label-lg);
  line-height: 1.5;
  color: var(--ink-2);
}

.gate__btn {
  margin-top: auto;
  width: 100%;
}

.gate__single {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  max-width: 320px;
}

/* 已豁免留痕 */
.gate__dl {
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: var(--sp-1) var(--sp-2);
  margin: 0;
}

.gate__dl dd {
  margin: 0;
  font-size: var(--fs-sub);
  color: var(--ink);
  word-break: break-word;
}

.gate__dl-sub {
  color: var(--ink-2);
}

.gate__record-note {
  margin-top: var(--sp-2);
  font-size: var(--fs-label-lg);
  color: var(--ink-3);
}

.gate__record .btn {
  margin-top: var(--sp-2);
}

.gate__foot {
  margin-top: var(--sp-3);
  padding-top: var(--sp-1);
  border-top: var(--hairline-w) solid var(--hairline);
}

.gate__foot-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.gate__quiet {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  font-size: var(--fs-label-lg);
  letter-spacing: 0.08em;
  color: var(--ink-3);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--hairline);
  transition: color var(--dur-fast) var(--ease-out);
}

.gate__quiet:hover {
  color: var(--ink);
}

.gate__foot-note {
  font-size: var(--fs-label);
  color: var(--ink-3);
}

.gate__confirm {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: var(--sp-1);
}

.gate__confirm-text {
  margin-right: auto;
  font-size: var(--fs-label-lg);
  color: var(--ink-2);
}

/* 打开 240ms spring；关闭 160ms */
.gate-enter-active .gate__panel {
  transition:
    transform var(--dur-slow) var(--ease-spring),
    opacity var(--dur-slow) var(--ease-out);
}

.gate-enter-from .gate__panel {
  transform: scale(0.96);
  opacity: 0;
}

.gate-leave-active .gate__panel {
  transition:
    transform 160ms var(--ease-in-out),
    opacity 160ms var(--ease-out);
}

.gate-leave-to .gate__panel {
  transform: scale(0.98);
  opacity: 0;
}

.gate-enter-active .gate__scrim,
.gate-leave-active .gate__scrim {
  transition:
    backdrop-filter var(--dur-slow) var(--ease-out),
    opacity var(--dur-slow) var(--ease-out);
}

.gate-enter-from .gate__scrim,
.gate-leave-to .gate__scrim {
  backdrop-filter: blur(0);
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .gate__rule,
  .gate__seal {
    animation: none;
  }

  .gate-enter-active .gate__panel,
  .gate-leave-active .gate__panel,
  .gate-enter-active .gate__scrim,
  .gate-leave-active .gate__scrim {
    transition: none;
  }
}
</style>
