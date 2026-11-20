<script setup lang="ts">
import { onActivated, onDeactivated, onMounted, onScopeDispose, watch } from 'vue'
import type { RunPreview } from './model'
import { relatedRun, useRuns } from './runs'
import { readHistoryLocation, replaceHistoryLocation } from './historyLocation'
defineEmits<{ open: [run: RunPreview] }>()
const props = defineProps<{ initialQuery?: string }>()
const { items, loading, error, nextCursor, query, framework, state, load } = useRuns()
const initial = readHistoryLocation(location.hash)
framework.value = initial?.framework ?? '全部框架'; state.value = initial?.state ?? '全部状态'
let applied = { source: 'api' as const, framework: framework.value, state: state.value }
function search() { applied = { source: 'api', framework: framework.value, state: state.value }; replaceHistoryLocation(applied); void load() }
function lookup(value?: string) {
  query.value = value ?? ''
  if (value) { framework.value = '全部框架'; state.value = '全部状态' }
  search()
}
onMounted(() => lookup(props.initialQuery))
onActivated(() => replaceHistoryLocation(applied))
function restoreHistory() {
  const restored = readHistoryLocation(location.hash)
  if (!restored || restored.source !== 'api') return
  if (restored.framework === applied.framework && restored.state === applied.state) return
  framework.value = restored.framework; state.value = restored.state; query.value = ''
  search()
}
function listen() { window.addEventListener('popstate', restoreHistory); window.addEventListener('workspace-location', restoreHistory) }
function unlisten() { window.removeEventListener('popstate', restoreHistory); window.removeEventListener('workspace-location', restoreHistory) }
onMounted(listen); onActivated(listen); onDeactivated(unlisten); onScopeDispose(unlisten)
watch(() => props.initialQuery, lookup)
const states = { open: '进行中', closed: '已结束', degraded: '降级运行' }
</script>
<template>
  <section class="service-run-history" aria-label="账本运行查询">
    
    <p class="muted">刷新保留已应用的来源、框架与生命周期，并重新查询授权数据；搜索文字、结果和分页游标不写入地址。</p>
    <form class="history-filters" @submit.prevent="search"><label>查询运行<input v-model="query" type="search" placeholder="任务名称或运行编号" :disabled="loading" /></label><label>服务运行框架<select v-model="framework" :disabled="loading"><option>全部框架</option><option>openJiuwen</option><option>LangGraph</option><option>OpenAI Agents SDK</option></select></label><label>生命周期<select v-model="state" :disabled="loading"><option>全部状态</option><option value="open">进行中</option><option value="closed">已结束</option><option value="degraded">降级运行</option></select></label><button class="secondary" :disabled="loading">查询运行</button></form>
    <p v-if="error" class="approval-error approval-feedback" role="alert">{{ error }}<button class="text-action" :disabled="loading" @click="search">重新查询</button></p>
    <p v-if="loading" role="status">正在查询运行…</p>
    <p v-if="!loading && !error && !items.length" class="workspace-empty" role="status">当前条件下没有可见运行。可以修改筛选后重新查询。</p>
    <ul class="history-list"><li v-for="item in items" :key="item.id"><button class="history-card" @click="$emit('open', item)"><span class="history-number">{{ item.id }}</span><strong>{{ item.title }}</strong><span class="history-meta">{{ item.framework }} · {{ states[item.state] }} · 第 {{ item.segment }} 段 · {{ item.decision_count }} 项决策</span><span class="history-meta">开始 {{ item.started_at }} · {{ item.completed_at ? `结束 ${item.completed_at}` : '尚未结束' }}</span></button><details v-if="item.parent_run_id || item.child_run_ids.length" class="reading-explanation"><summary>查看委派关系</summary><p v-if="item.parent_run_id">父运行 <button class="text-action" @click="$emit('open', relatedRun(item.parent_run_id, item.framework))">{{ item.parent_run_id }}</button></p><p v-for="id in item.child_run_ids" :key="id">子运行 <button class="text-action" @click="$emit('open', relatedRun(id, item.framework))">{{ id }}</button></p></details></li></ul>
    <button v-if="nextCursor" class="secondary" :disabled="loading || !!error" @click="load(true)">加载更多运行</button>
  </section>
</template>
