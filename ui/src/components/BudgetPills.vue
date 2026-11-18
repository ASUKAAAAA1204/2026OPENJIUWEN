<script setup lang="ts">
import { computed } from 'vue'
import type { Plan, PlanTier } from '../api/types'
import { bandText, fmtCu } from '../utils/format'
import { useTween } from '../composables/useTween'

const props = defineProps<{ plan: Plan; activeCu: number | null }>()
const emit = defineEmits<{ 'action:select-budget': [cu: number] }>()

const tiers = computed(() => props.plan.tiers)

/** 未指定或不在点列里时回落到完整方案（末项） */
const activeIndex = computed(() => {
  const index = tiers.value.findIndex((tier) => tier.cu === props.activeCu)
  return index === -1 ? tiers.value.length - 1 : index
})

const activeTier = computed<PlanTier | null>(() => tiers.value[activeIndex.value] ?? null)

const hasPrediction = computed(() => activeTier.value?.predicted != null)

const { display } = useTween(() => activeTier.value?.predicted ?? 0, 300)

function pick(tier: PlanTier) {
  if (tier.cu === activeTier.value?.cu) return
  emit('action:select-budget', tier.cu)
}
</script>

<template>
  <section class="budget panel" aria-label="预算档位">
    <header class="budget__head">
      <h2 class="budget__title serif">预算档位</h2>
      <span class="budget__meta">
        <span class="label label--latin budget__tag">Budget ·</span>
        <span class="label">单位 CU</span>
      </span>
    </header>

    <p v-if="tiers.length === 0" class="budget__empty">暂无预算档位 · 预测未出</p>

    <template v-else>
      <div
        class="budget__pills"
        role="group"
        aria-label="预算档位，单位 CU"
        :style="{ '--count': tiers.length }"
      >
        <span
          class="budget__slider"
          aria-hidden="true"
          :style="{ '--index': activeIndex }"
        />
        <button
          v-for="(tier, i) in tiers"
          :key="tier.cu"
          type="button"
          class="budget__pill"
          :class="{ 'is-active': i === activeIndex }"
          :aria-label="`${fmtCu(tier.cu)} CU`"
          :aria-pressed="i === activeIndex"
          @click="pick(tier)"
        >
          <span class="budget__num mono">{{ fmtCu(tier.cu) }}</span>
        </button>
      </div>

      <p class="budget__readout">
        <span class="label budget__readout-label">预测</span>
        <template v-if="hasPrediction">
          <span class="budget__predicted serif">{{ display }}</span>
          <span class="budget__unit">分</span>
        </template>
        <span v-else class="budget__pending">预测未出</span>
        <span class="budget__target">目标 {{ bandText(plan.target_band) }}</span>
      </p>
    </template>
  </section>
</template>

<style scoped>
.budget {
  padding: var(--sp-3) var(--sp-4) var(--sp-2);
}

.budget__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.budget__title {
  flex: none;
  font-size: var(--fs-title);
  color: var(--ink);
  white-space: nowrap;
}

.budget__meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

@media (max-width: 1120px) {
  .budget__tag {
    display: none;
  }
}

.budget__empty {
  padding: var(--sp-2) 0 var(--sp-1);
  font-size: var(--fs-sub);
  color: var(--ink-3);
}

/* 五档等分；选中滑块用共享元素平移，不停留中间态 */
.budget__pills {
  position: relative;
  display: grid;
  grid-template-columns: repeat(var(--count), 1fr);
  margin-top: var(--sp-2);
}

.budget__pill {
  height: 36px;
  color: var(--ink-3);
  transition: color var(--dur-fast) var(--ease-out);
}

.budget__pill:hover {
  color: var(--ink-2);
}

.budget__pill.is-active {
  color: var(--ink);
}

.budget__num {
  font-size: var(--fs-body);
}

.budget__slider {
  position: absolute;
  left: 0;
  bottom: 0;
  width: calc(100% / var(--count));
  height: 2px;
  background: var(--ink);
  transform: translateX(calc(var(--index) * 100%));
  transition: transform 220ms var(--ease-in-out);
}

.budget__readout {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: var(--sp-2);
}

.budget__readout-label {
  margin-right: 2px;
}

.budget__predicted {
  font-size: 28px;
  line-height: 1;
  color: var(--ink);
}

.budget__unit {
  font-size: var(--fs-sub);
  color: var(--ink-2);
}

.budget__pending {
  font-size: var(--fs-sub);
  color: var(--ink-3);
}

.budget__target {
  margin-left: auto;
  font-size: var(--fs-label-lg);
  letter-spacing: 0.08em;
  color: var(--ink-2);
  white-space: nowrap;
}

/* 720p 投影：预测读数与档位并排，省掉一整行纵向高度 */
@media (max-height: 960px) {
  .budget {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      "head head"
      "pills readout";
    column-gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-3) var(--sp-1);
  }

  .budget__head {
    grid-area: head;
    padding-bottom: var(--sp-1);
  }

  .budget__pills {
    grid-area: pills;
    margin-top: var(--sp-1);
  }

  .budget__readout {
    grid-area: readout;
    align-self: center;
    margin-top: var(--sp-1);
  }

  .budget__empty {
    grid-area: pills;
  }
}
</style>
