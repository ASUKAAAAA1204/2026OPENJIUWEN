<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import CoordBar from './components/CoordBar.vue'
import ScoreCard from './components/ScoreCard.vue'
import DimensionBars from './components/DimensionBars.vue'
import DeductionList from './components/DeductionList.vue'
import PlanTable from './components/PlanTable.vue'
import BudgetPills from './components/BudgetPills.vue'
import CostCurve from './components/CostCurve.vue'
import EvidenceDrawer from './components/EvidenceDrawer.vue'
import GateDialog from './components/GateDialog.vue'
import type { Dim } from './api/types'
import { useReport } from './composables/useReport'
import { exportFileName } from './utils/format'
import { ackReasonLabel, type AckRecord } from './utils/ack'
import { CLAIM_FIXTURE } from './mock/claims'

const { report, loading, error, load, source } = useReport()

const DIM_KEYS = ['L', 'C', 'R', 'D', 'F'] as const

/** ?drawer=L 供截图与演示直接打开抽屉 */
function initialDrawer(): Dim['key'] | null {
  const value = new URLSearchParams(window.location.search).get('drawer')
  return DIM_KEYS.includes(value as Dim['key']) ? (value as Dim['key']) : null
}

const drawerKey = ref<Dim['key'] | null>(initialDrawer())

/** null = 完整方案（由 BudgetPills 回落到 last tier） */
const budgetCu = ref<number | null>(null)

/** ?gate=1 供截图与演示直接打开闸门弹窗 */
const gateOpen = ref(new URLSearchParams(window.location.search).get('gate') === '1')

/** 豁免留痕：存在即表示本次发布已走豁免分支 */
const exemption = ref<AckRecord | null>(null)

const publishLabel = computed(() => (exemption.value ? '查看豁免记录' : '发起发布'))

/** PARTIAL 禁用发布决策（策划书 §7.2） */
const publishDisabled = computed(
  () => !report.value || (report.value.doc_state === 'PARTIAL' && exemption.value === null),
)

function onPublish() {
  if (publishDisabled.value) return
  gateOpen.value = true
}

/** 执行修复：按演示流程切到 post，闸门弹窗自动转为放行态 */
function onRepair() {
  if (!report.value) return
  void load({ ...source.value, scenario: 'post' })
}

function onAck(record: AckRecord) {
  exemption.value = record
  gateOpen.value = false
}

const drawerDim = computed(() => report.value?.dims.find((d) => d.key === drawerKey.value) ?? null)
const drawerClaims = computed(() =>
  drawerKey.value === null ? [] : CLAIM_FIXTURE.filter((c) => c.dim === drawerKey.value),
)

const stage = ref(0)
let timers: number[] = []
let skipHandler: (() => void) | null = null

const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const lowPerf = () =>
  typeof navigator !== 'undefined' && navigator.hardwareConcurrency !== undefined
    ? navigator.hardwareConcurrency <= 4
    : false

onMounted(() => {
  if (reducedMotion() || lowPerf()) {
    stage.value = 3
    return
  }
  requestAnimationFrame(() => {
    stage.value = 1
    timers.push(window.setTimeout(() => (stage.value = 2), 200))
    timers.push(window.setTimeout(() => (stage.value = 3), 420))
  })
  skipHandler = () => {
    stage.value = 3
    timers.forEach((t) => window.clearTimeout(t))
    timers = []
    window.removeEventListener('pointerdown', skipHandler as EventListener)
    skipHandler = null
  }
  window.addEventListener('pointerdown', skipHandler)
})

onBeforeUnmount(() => {
  timers.forEach((t) => window.clearTimeout(t))
  if (skipHandler) window.removeEventListener('pointerdown', skipHandler)
})

const gateHint = computed(() => {
  if (!report.value) return ''
  if (report.value.doc_state === 'PARTIAL') return '样本不足，不可发布决策'
  if (report.value.doc_state === 'DESIGN_ONLY') return '仅设计态，先完成规则改写'
  return `门槛 ${report.value.gate.threshold} · 当前 ${report.value.score}`
})

function onExport() {
  if (!report.value) return
  const { coord } = report.value
  const blob = new Blob([JSON.stringify(report.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = exportFileName(coord.skill_version, coord.model)
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="stage">
    <div class="deco" :class="{ 'is-on': stage >= 1 }" aria-hidden="true">
      <div class="deco__grid" />
      <div class="deco__ticks" />
      <div class="deco__vtext">Audit Readiness</div>
    </div>

    <div class="page" :class="{ 'has-ack': exemption }">
      <p v-if="loading && !report" class="page__state label">载入中</p>
      <p v-else-if="error" class="page__state page__state--error">{{ error }}</p>

      <template v-else-if="report">
        <div class="stage-enter" :class="{ 'is-in': stage >= 2 }">
          <CoordBar :coord="report.coord" :doc-state="report.doc_state" @action:export="onExport" />
        </div>

        <div v-if="exemption" class="ack-bar" role="status">
          <span class="ack-bar__text">
            已豁免 · 原因码 <span class="mono">{{ exemption.code }}</span> ·
            <span class="mono">{{ exemption.date }}</span>
          </span>
          <span class="ack-bar__sub">
            {{ ackReasonLabel(exemption.code)
            }}<template v-if="exemption.note"> · {{ exemption.note }}</template>
          </span>
        </div>

        <main class="layout">
          <div class="layout__left stage-enter" :class="{ 'is-in': stage >= 3 }">
            <ScoreCard
              :score="report.score"
              :band="report.band"
              :counts="report.counts"
              :doc-state="report.doc_state"
              :threshold="report.gate.threshold"
            />
            <DimensionBars :dims="report.dims" clickable @action:select-dim="drawerKey = $event" />
            <DeductionList
              :deductions="report.deductions"
              @action:select-deduction="drawerKey = $event"
            />
          </div>

          <div class="layout__right stage-enter" :class="{ 'is-in': stage >= 3 }">
            <PlanTable :plan="report.plan" />
            <BudgetPills
              :plan="report.plan"
              :active-cu="budgetCu"
              @action:select-budget="budgetCu = $event"
            />
            <CostCurve
              :trend="report.trend"
              :threshold="report.gate.threshold"
              :marginal-from="report.marginal_exhausted_from"
              :tiers="report.plan.tiers"
              :active-cu="budgetCu"
            />
          </div>
        </main>

        <footer class="opsbar">
          <span class="opsbar__hint mono">{{ gateHint }}</span>
          <button
            type="button"
            class="btn btn--primary"
            :disabled="publishDisabled"
            @click="onPublish"
          >
            {{ publishLabel }}
          </button>
        </footer>

        <EvidenceDrawer
          :open="drawerKey !== null"
          :dim-label="drawerDim?.label ?? ''"
          :dim-num="drawerDim?.num ?? 0"
          :dim-den="drawerDim?.den ?? 0"
          :dim-state="drawerDim?.state ?? 'MISSING'"
          :claims="drawerClaims"
          @action:close="drawerKey = null"
        />

        <GateDialog
          :open="gateOpen"
          :decision="report.gate.decision"
          :reason-code="report.gate.reason_code"
          :score="report.score"
          :band="report.band"
          :threshold="report.gate.threshold"
          :doc-state="report.doc_state"
          :plan="report.plan"
          :record="exemption"
          @action:repair="onRepair"
          @action:release="gateOpen = false"
          @action:ack="onAck"
          @action:close="gateOpen = false"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.stage {
  position: relative;
  min-height: 100%;
}

.page {
  position: relative;
  z-index: var(--z-panel);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  max-width: 1440px;
  margin: 0 auto;
  padding: var(--sp-4) var(--sp-6) var(--sp-6);
}

.page__state {
  padding: var(--sp-8) 0;
}

.page__state--error {
  color: var(--danger);
}

/* 豁免留痕 banner：常驻，不可关闭 */
.ack-bar {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px var(--sp-2);
  margin-top: var(--sp-2);
  padding: var(--sp-1) var(--sp-2);
  border-left: 2px solid var(--warning);
  background: var(--surface);
}

.ack-bar__text {
  font-size: var(--fs-label-lg);
  letter-spacing: 0.04em;
  color: var(--ink);
}

.ack-bar__sub {
  font-size: var(--fs-label-lg);
  color: var(--ink-3);
}

.layout {
  display: grid;
  grid-template-columns: 4fr 8fr;
  gap: var(--sp-4);
  margin-top: var(--sp-4);
}

.layout__left {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-width: 0;
}

.layout__right {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-width: 0;
}

.opsbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-3);
  margin-top: auto;
  padding-top: var(--sp-2);
  border-top: var(--hairline-w) solid var(--hairline);
}

.opsbar__hint {
  font-size: var(--fs-label-lg);
  color: var(--ink-2);
}

/* 720p 投影：压缩纵向节奏，让操作条留在折叠线以上（§10.3） */
@media (max-height: 960px) {
  .page {
    padding: var(--sp-1) var(--sp-6);
  }

  .ack-bar {
    margin-top: var(--sp-1);
    padding-block: 2px;
  }

  .layout {
    margin-top: var(--sp-2);
    gap: var(--sp-2);
  }

  .layout__left,
  .layout__right {
    gap: var(--sp-2);
  }

  .opsbar {
    padding-top: var(--sp-1);
  }

  /* 豁免 banner 常驻后仍要保住一屏：整页再收紧 */
  .page.has-ack .layout {
    margin-top: var(--sp-1);
  }

  .page.has-ack .layout__left,
  .page.has-ack .layout__right {
    gap: var(--sp-1);
  }

  .page.has-ack .ack-bar {
    padding-block: 0;
  }
}
</style>
