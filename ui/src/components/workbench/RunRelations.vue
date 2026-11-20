<script setup lang="ts">
import { computed } from 'vue'
import type { AuditSnapshot } from './auditSource'
import { decisionClassKey } from './decisionClasses'
const props = defineProps<{ snapshot: AuditSnapshot }>()
defineEmits<{ open: [id: string] }>()
const delegations = computed(() => props.snapshot.decisions.filter(item => decisionClassKey(item.class) === 'delegate'))
</script>
<template>
  <section class="detail-panel run-relations" aria-label="委派关联">
    <div class="section-heading"><h2>委派关联</h2><span class="outline-label">{{ snapshot.child_run_ids.length }} 个直接子运行</span></div>
    <p class="muted">子运行独立记账；父运行保留自身决策、委派关系与失败结果。子运行的结果不覆盖父级读数，聚合须按决策标识去重。</p>
    <ol class="relation-chain" aria-label="直接责任链">
      <li v-if="snapshot.parent_run_id"><span class="eyebrow">PARENT / 父运行</span><button class="text-action" @click="$emit('open', snapshot.parent_run_id!)">{{ snapshot.parent_run_id }}</button><small>权限与状态打开后重新读取</small></li>
      <li v-else><span class="eyebrow">PARENT / 父运行</span><strong>无父运行</strong><small>当前快照未记录上级</small></li>
      <li class="relation-current"><span class="eyebrow">CURRENT / 当前运行</span><strong>{{ snapshot.run_id }}</strong><small>第 {{ snapshot.segment }} 段 · {{ ({ open: '进行中', closed: '已关闭', degraded: '降级运行', preview: '界面预览' })[snapshot.state] }}</small></li>
      <li><span class="eyebrow">CHILDREN / 直接子运行</span><ul v-if="snapshot.child_run_ids.length"><li v-for="id in snapshot.child_run_ids" :key="id"><button class="text-action" @click="$emit('open', id)">子运行 {{ id }}</button></li></ul><strong v-else>暂无子运行</strong><small>状态与执行结果尚未在当前快照中采集</small></li>
    </ol>
    <details class="reading-explanation"><summary>当前运行的委派决策 · {{ delegations.length }} 条</summary>
      
      <ul v-if="delegations.length" class="relation-decisions"><li v-for="item in delegations" :key="item.decision_id"><strong>{{ item.decision_id }}</strong><span>Gate {{ item.gate }} · 结果状态 {{ item.status }}</span><span>第 {{ item.segment }} 段 · {{ item.gate_reason }}</span></li></ul>
      <p v-else class="muted">当前运行未记录 delegate 类决策；有子运行关联也不能替代委派事件证据。</p>
    </details>
    <p class="muted">这里只展示当前快照的直接关系，不推断全图或委派深度。PRD 深度上限为3，由服务端拒绝超限并记账。打开关联运行会重新核验访问权限。</p>
    <p class="muted">{{ snapshot.chain.reason }}</p>
  </section>
</template>
