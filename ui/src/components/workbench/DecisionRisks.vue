<script setup lang="ts">
import { computed } from 'vue'
import type { DecisionRecord } from './audit'
const props = defineProps<{ records: DecisionRecord[] }>()
defineEmits<{ locate: [status: string] }>()
const unhooked = computed(() => props.records.filter(item => item.status === 'UNHOOKED').length)
const unknown = computed(() => props.records.filter(item => item.status === 'UNKNOWN_OUTCOME').length)
</script>
<template>
  <section v-if="unhooked || unknown" class="decision-risk-panel" aria-label="需要核对的动作" role="alert">
    <h2>需要核对的动作</h2>
    <div v-if="unhooked"><strong>{{ unhooked }} 条 UNHOOKED · 未命中白名单</strong><p>这些副作用动作没有进入规则判定，不能把 Gate NONE 当作放行。是否执行及执行结果须另查事件证据；请核对白名单版本和工具登记。</p><button class="text-action" @click="$emit('locate', 'UNHOOKED')">定位未覆盖动作</button></div>
    <div v-if="unknown"><strong>{{ unknown }} 条 UNKNOWN_OUTCOME · 执行结果未知</strong><button class="text-action" @click="$emit('locate', 'UNKNOWN_OUTCOME')">定位结果未知动作</button></div>
    <p>这里显示当前运行的异常条数，不将全部记录数当成覆盖率分母。版本级未覆盖率与超过5%的告警以版本评估口径为准。</p>
  </section>
</template>
