<script setup lang="ts">
import { computed } from 'vue'
import type { Deduction, Dim } from '../api/types'
import { fmtCu } from '../utils/format'

const props = defineProps<{ deductions: Deduction[] }>()
const emit = defineEmits<{ 'action:select-deduction': [dim: Dim['key']] }>()

/** 最多 3 条：一屏只给最痛的三个缺口 */
const rows = computed(() => props.deductions.slice(0, 3))

function pick(dim: string) {
  emit('action:select-deduction', dim as Dim['key'])
}
</script>

<template>
  <section class="ded panel">
    <header class="ded__head">
      <h2 class="ded__title serif">扣分项</h2>
      <span class="label ded__meta">Deductions · {{ deductions.length }} 项</span>
    </header>

    <p v-if="rows.length === 0" class="ded__empty">暂无扣分项</p>

    <ul v-else class="ded__list">
      <li v-for="(row, i) in rows" :key="`${row.dim}-${i}`">
        <button type="button" class="ded__row" @click="pick(row.dim)">
          <span class="ded__text">{{ row.text }}</span>
          <span class="ded__row-meta">
            <span class="ded__action">{{ row.action }}</span>
            <span class="ded__dot">·</span>
            <span class="mono">{{ fmtCu(row.price_cu) }} CU</span>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.ded {
  padding: var(--sp-3) var(--sp-4);
}

.ded__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: var(--hairline-w) solid var(--hairline);
}

.ded__title {
  flex: none;
  font-size: var(--fs-title);
  color: var(--ink);
  white-space: nowrap;
}

.ded__meta {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ded__empty {
  padding: var(--sp-2) 0 var(--sp-1);
  font-size: var(--fs-sub);
  color: var(--ink-3);
}

.ded__list {
  margin-top: var(--sp-1);
}

.ded__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-3);
  width: 100%;
  min-height: 40px;
  padding: var(--sp-1) 0;
  text-align: left;
  border-bottom: var(--hairline-w) solid var(--hairline);
  transition: background-color var(--dur-fast) var(--ease-out);
}

.ded__row:last-child {
  border-bottom: 0;
}

.ded__row:hover {
  background: var(--blue-wash);
}

.ded__text {
  font-size: var(--fs-sub);
  color: var(--ink);
}

.ded__row-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex: none;
  font-size: var(--fs-label-lg);
  color: var(--ink-2);
  white-space: nowrap;
}

.ded__action {
  color: var(--ink-2);
}

.ded__dot {
  color: var(--ink-3);
}

/* 1024 底线：左栏变窄时去掉动作名，只留数量与 CU，正文与 meta 首行对齐 */
@media (max-width: 1120px) {
  .ded__row {
    align-items: flex-start;
    gap: var(--sp-2);
  }

  .ded__action,
  .ded__dot {
    display: none;
  }

  .ded__row-meta {
    padding-top: 1px;
  }
}

/* 720p 投影：行高 40→32，面板纵向留白收紧 */
@media (max-height: 960px) {
  .ded {
    padding: var(--sp-2) var(--sp-3) var(--sp-1);
  }

  .ded__head {
    padding-bottom: var(--sp-1);
  }

  .ded__row {
    min-height: 32px;
    padding-block: 4px;
  }
}
</style>
