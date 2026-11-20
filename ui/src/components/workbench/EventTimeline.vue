<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { eventLabels, parseEvents, type DecisionEvent } from './events'
const props = defineProps<{ runId: string; decisionId: string }>()
const events = ref<DecisionEvent[]>([]), loading = ref(false), error = ref('')
let active: AbortController | undefined
async function reload() {
  active?.abort()
  const controller = new AbortController(); active = controller
  events.value = []; error.value = ''; loading.value = true
  const timeout = setTimeout(() => controller.abort(), 12000)
  try {
    const response = await fetch(`/api/v27/runs/${encodeURIComponent(props.runId)}/decisions/${encodeURIComponent(props.decisionId)}/events`, { signal: controller.signal, credentials: 'same-origin', cache: 'no-store' })
    if (!response.ok) throw new Error(({ 401: '登录已失效，请重新登录。', 403: '无权读取此决策事件。', 404: '事件记录或服务接口尚不可用。' } as Record<number, string>)[response.status] ?? '事件服务暂时不可用。')
    const result = parseEvents(await response.json(), props.runId, props.decisionId)
    if (active === controller) events.value = result
  } catch (cause) {
    if (active === controller) error.value = controller.signal.aborted ? '读取超时，请重试。' : cause instanceof Error ? cause.message : '无法读取事件。'
  } finally { clearTimeout(timeout); if (active === controller) loading.value = false }
}
watch(() => [props.runId, props.decisionId], reload, { immediate: true })
onScopeDispose(() => { const previous = active; active = undefined; previous?.abort() })
</script>
<template>
  <section class="event-timeline" aria-label="决策事件时间线">
    <h3>决策事件时间线</h3><p class="muted">策略允许和人工批准只代表执行权限；工具完成以结果事件为准。按账本追加序号排列。</p>
    <p v-if="loading" role="status">正在读取事件…</p>
    <div v-else-if="error" role="alert"><p>{{ error }}</p><button class="secondary" @click="reload">重新读取事件</button></div>
    <template v-else>
      
      <p v-if="!events.length" class="muted">暂无事件</p><ol v-else><li v-for="event in events" :key="event.event_id"><span class="eyebrow">SEQ {{ event.seq }} · {{ event.recorded_at ?? '未记录时间' }}</span><h4>{{ eventLabels[event.type] }}</h4><p>{{ event.summary }}</p><p v-if="event.type === 'UNKNOWN_OUTCOME'" class="inspector-note">结果未知，需人工核查；请勿直接重复执行，以免产生重复副作用。</p></li></ol>
      <button class="text-action" @click="reload">刷新事件</button>
    </template>
  </section>
</template>
