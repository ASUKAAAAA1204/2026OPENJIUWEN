<script setup lang="ts">
import { computed } from 'vue'
import type { DocState } from '../api/types'
import { docStateText } from '../utils/format'

const props = defineProps<{ state: DocState }>()

const tone = computed(() => {
  if (props.state === 'COMPLETE') return 'ok'
  if (props.state === 'PARTIAL') return 'warn'
  return 'muted'
})
</script>

<template>
  <span class="badge" :class="[`badge--${tone}`, { 'badge--dim': state === 'PARTIAL' }]">
    <svg class="badge__icon" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
      <path
        v-if="state === 'COMPLETE'"
        d="M2 6.4 4.6 9 10 3.4"
        fill="none"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="square"
      />
      <template v-else-if="state === 'PARTIAL'">
        <rect x="1" y="1" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.2" />
        <path d="M6 1v10" stroke="currentColor" stroke-width="1.2" />
        <rect x="1" y="1" width="5" height="10" fill="currentColor" />
      </template>
      <rect
        v-else
        x="1.5"
        y="1.5"
        width="9"
        height="9"
        fill="none"
        stroke="currentColor"
        stroke-width="1.2"
        stroke-dasharray="2 2"
      />
    </svg>
    <span class="badge__text">{{ docStateText(state) }}</span>
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border: var(--hairline-w) solid var(--hairline);
  font-size: var(--fs-label);
  letter-spacing: 0.12em;
  white-space: nowrap;
  transition:
    color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    filter var(--dur-fast) var(--ease-out);
}

.badge--ok {
  color: var(--success);
  border-color: var(--success);
}

.badge--warn {
  color: var(--warning);
  border-color: var(--warning);
}

.badge--muted {
  color: var(--ink-2);
}

/* PARTIAL 整体降饱和，避免与 COMPLETE 抢注意力 */
.badge--dim {
  filter: saturate(0.55);
  opacity: 0.9;
}
</style>
