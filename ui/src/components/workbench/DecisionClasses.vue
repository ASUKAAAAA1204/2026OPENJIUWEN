<script setup lang="ts">
import { computed } from 'vue'
import type { DecisionRecord } from './audit'
import { decisionClassCounts } from './decisionClasses'
const props = defineProps<{ records: DecisionRecord[]; preview: boolean; modelValue: string }>()
defineEmits<{ 'update:modelValue': [value: string] }>()
const counts = computed(() => decisionClassCounts(props.records))
</script>
<template>
  <section class="detail-panel decision-classes" aria-label="决策类别分布">
    <div class="section-heading"><h2>决策类别分布</h2><span class="outline-label">{{ preview ? '预览类别' : '本次出现' }} {{ counts.observed }} / 11</span></div>
    <details class="reading-explanation">
    <summary>查看全部类别与筛选{{ modelValue ? ' · 已应用类别筛选' : '' }}</summary>
    
    <button class="text-action" :aria-pressed="modelValue === ''" @click="$emit('update:modelValue', '')">全部类别 · {{ records.length }} 条</button>
    <div class="decision-class-grid">
      <button v-for="item in [...counts.known, ...counts.other]" :key="item.key" class="decision-class-card" :aria-pressed="modelValue === item.key" @click="$emit('update:modelValue', modelValue === item.key ? '' : item.key)">
        <span>{{ item.label }}</span><strong>{{ item.count }}</strong><small>{{ item.key.startsWith('other:') ? '白名单外或预览类别' : item.key }}</small>
      </button>
    </div>
    
    </details>
  </section>
</template>
