<script setup lang="ts">
import { computed } from 'vue'
import type { Coord, DocState } from '../api/types'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ coord: Coord; docState: DocState }>()
const emit = defineEmits<{ 'action:export': [] }>()

interface Segment {
  label: string
  value: string
  missing: boolean
}

const segments = computed<Segment[]>(() => {
  const rows: Array<[string, string]> = [
    ['Skill Version', props.coord.skill_version],
    ['Model', props.coord.model],
    ['Data Profile', props.coord.data_profile],
  ]
  return rows.map(([label, value]) => ({
    label,
    value: value.trim() === '' ? '未指定' : value,
    missing: value.trim() === '',
  }))
})
</script>

<template>
  <header class="coord">
    <dl class="coord__segs">
      <div v-for="seg in segments" :key="seg.label" class="coord__seg" :class="{ 'is-missing': seg.missing }">
        <dt class="label label--latin">{{ seg.label }}</dt>
        <dd class="coord__value mono">
          <svg v-if="seg.missing" class="coord__warn" viewBox="0 0 8 8" width="8" height="8" aria-hidden="true">
            <rect x="1" y="1" width="6" height="6" fill="currentColor" />
          </svg>
          {{ seg.value }}
        </dd>
      </div>
    </dl>

    <div class="coord__tail">
      <StatusBadge :state="docState" />
      <button type="button" class="btn" @click="emit('action:export')">导出 JSON</button>
    </div>
  </header>
</template>

<style scoped>
.coord {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-4);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.coord__segs {
  display: flex;
  align-items: flex-end;
  gap: var(--sp-6);
}

.coord__seg {
  min-width: 0;
}

.coord__value {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0 0;
  font-size: var(--fs-body);
  color: var(--ink);
  white-space: nowrap;
}

.coord__seg.is-missing .coord__value {
  color: var(--danger);
}

.coord__warn {
  color: currentColor;
  flex: none;
}

.coord__tail {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex: none;
}
</style>
