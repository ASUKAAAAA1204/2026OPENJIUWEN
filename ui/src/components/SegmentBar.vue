<script setup lang="ts">
import { computed } from 'vue'
import type { DimState } from '../api/types'

const props = defineProps<{
  num: number
  den: number
  state: DimState
  badge?: string
}>()

const ratio = computed(() => {
  if (props.state !== 'VALUE' || props.den <= 0) return 0
  return Math.max(0, Math.min(1, props.num / props.den))
})
</script>

<template>
  <div
    class="segbar"
    :class="`segbar--${state.toLowerCase()}`"
    :style="{ '--ratio': ratio }"
    aria-hidden="true"
  >
    <span class="segbar__ticks" />
    <span class="segbar__track" />
    <span v-if="state === 'VALUE'" class="segbar__fill" />
    <span v-if="badge" class="segbar__badge mono">{{ badge }}</span>
  </div>
</template>

<style scoped>
.segbar {
  position: relative;
  height: 14px;
  min-width: 64px;
}

/* 每 5% 一个刻度 */
.segbar__ticks {
  position: absolute;
  inset: 0;
  background-image: linear-gradient(to right, var(--grid) 1px, transparent 1px);
  background-size: 5% 100%;
  opacity: 0.9;
}

.segbar__track {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 2px;
  margin-top: -1px;
  background: var(--hairline);
}

/* VALUE：实心填充，从左侧生长（只动 transform） */
.segbar__fill {
  position: absolute;
  left: 0;
  top: 50%;
  width: 100%;
  height: 2px;
  margin-top: -1px;
  background: var(--ink-2);
  transform-origin: left center;
  transform: scaleX(var(--ratio));
  animation: segbar-grow 400ms var(--ease-out) var(--seg-delay, 0ms) both;
}

@keyframes segbar-grow {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(var(--ratio));
  }
}

/* NA：虚线空轨道 + 文案（文案由父组件给出） */
.segbar--na .segbar__track {
  background: none;
  background-image: repeating-linear-gradient(90deg, var(--ink-3) 0 4px, transparent 4px 8px);
}

/* MISSING：45° 斜纹，静止不做动画 */
.segbar--missing .segbar__track {
  height: 8px;
  margin-top: -4px;
  background: repeating-linear-gradient(45deg, var(--ink-3) 0 1px, transparent 1px 6px);
  opacity: 0.7;
}

.segbar--missing .segbar__ticks,
.segbar--na .segbar__ticks {
  background-image: linear-gradient(to right, var(--hairline) 1px, transparent 1px);
}

.segbar__badge {
  position: absolute;
  right: 0;
  top: -9px;
  padding: 0 4px;
  font-size: var(--fs-label);
  line-height: 12px;
  color: var(--ink-3);
  background: var(--bg);
  border: var(--hairline-w) solid var(--hairline);
  white-space: nowrap;
}
</style>
