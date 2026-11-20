<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { ExecutionError, parseExecutionState, streamRunExecution, type ExecutionEvent } from './execution'
const props = defineProps<{ runId: string; title: string; segment: number; state: string; locked?: boolean }>()
const emit = defineEmits<{ busy: [value: boolean]; approval: [id: string | null] }>()
const busy = ref(false), status = ref<'idle' | 'connecting' | 'streaming' | 'reconnecting' | 'completed' | 'failed' | 'unknown' | 'review'>('idle')
const events = ref<ExecutionEvent[]>([]), output = ref(''), error = ref(''), approvalId = ref('')
const outputTruncated = ref(false), eventsTruncated = ref(false)
const requestId = ref(''), lastEventId = ref(0), turnId = ref('')
const toolOutcomeUnknown = ref(false)
const checking = ref(true), stateError = ref(''), executionRevision = ref(''), restored = ref(false)
const statusLabel = computed(() => checking.value ? '核对执行状态' : stateError.value || !executionRevision.value ? '状态待确认' : ({ idle: '未启动', connecting: '正在连接', streaming: '运行中', reconnecting: '正在重连', completed: '本轮完成', failed: '本轮失败', unknown: '结果待核对', review: '结果待核对' })[status.value])
const canStart = computed(() => !!executionRevision.value && !checking.value && !stateError.value && !busy.value && !props.locked && !['closed'].includes(props.state) && status.value === 'idle')
watch(busy, value => emit('busy', value), { flush: 'sync' })
watch(approvalId, value => emit('approval', value || null), { flush: 'sync' })
let revision = 0, controller: AbortController | undefined
async function loadState() {
  if (busy.value || props.locked || props.state === 'closed') { checking.value = false; return }
  const token = ++revision, active = new AbortController(); controller?.abort(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  checking.value = true; stateError.value = ''
  try {
    const response = await fetch(`/api/v27/runs/${encodeURIComponent(props.runId)}/execution-state?segment=${props.segment}`, { signal: active.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
    if (!response.ok) throw new Error(({ 401: '登录已失效。', 403: '当前账号无权查看执行状态。', 404: '执行状态接口尚未提供。' } as Record<number, string>)[response.status] ?? '暂时无法读取执行状态。')
    const result = parseExecutionState(await response.json(), props.runId, props.segment)
    if (token !== revision) return
    executionRevision.value = result.revision; requestId.value = result.request_id ?? ''
    status.value = result.status === 'active' ? 'unknown' : result.status
    restored.value = result.status !== 'idle'
    lastEventId.value = 0; turnId.value = ''
    if (status.value === 'unknown') error.value = '此运行已有执行请求。请连接同一请求核对进度，不要重新启动。'
    else if (status.value === 'review') error.value = '服务端报告工具结果待核对。请查看运行账本和外部结果，勿重复执行。'
  } catch (cause) {
    if (token === revision) stateError.value = `${active.signal.aborted ? '执行状态读取超时。' : cause instanceof Error ? cause.message : '无法读取执行状态。'} 尚未确认可启动，请重新读取。`
  } finally { clearTimeout(timer); if (token === revision) checking.value = false }
}
function accept(event: ExecutionEvent) {
  status.value = 'streaming'
  lastEventId.value = event.seq
  if (!turnId.value) turnId.value = event.turn_id
  if (event.type === 'assistant_delta') {
    const combined = `${output.value}${event.text}`
    if (combined.length > 50000) outputTruncated.value = true
    output.value = combined.slice(-50000)
  }
  if (event.type === 'approval_requested') approvalId.value = event.approval_id
  if (event.type === 'tool_result' && event.status === 'UNKNOWN_OUTCOME') toolOutcomeUnknown.value = true
  if (event.type === 'tool_result' && events.value.some(item => item.type === 'approval_requested' && item.approval_id === approvalId.value && item.decision_id === event.decision_id)) approvalId.value = ''
  if (event.type === 'turn_completed' || event.type === 'turn_failed') approvalId.value = ''
  if (event.type !== 'assistant_delta') {
    if (events.value.length >= 100) eventsTruncated.value = true
    events.value = [...events.value, event].slice(-100)
  }
}
async function execute(reconnect = false) {
  if (busy.value || checking.value || stateError.value || (reconnect ? status.value !== 'unknown' || !requestId.value : !canStart.value)) return
  if (!reconnect) {
    requestId.value = crypto.randomUUID(); lastEventId.value = 0; turnId.value = ''; events.value = []; output.value = ''; error.value = ''; approvalId.value = ''
    outputTruncated.value = false; eventsTruncated.value = false
    toolOutcomeUnknown.value = false
  }
  const token = ++revision, active = new AbortController(); controller = active
  busy.value = true; status.value = reconnect ? 'reconnecting' : 'connecting'; error.value = ''
  try {
    const result = await streamRunExecution(
      { runId: props.runId, segment: props.segment, task: props.title, requestId: requestId.value, expectedExecutionRevision: executionRevision.value },
      active.signal,
      accept,
      () => { if (token === revision) status.value = 'reconnecting' },
      lastEventId.value,
      turnId.value || null,
    )
    if (token !== revision) return
    status.value = result.terminal.type === 'turn_completed' ? 'completed' : result.terminal.outcome === 'unknown' ? 'unknown' : 'failed'
    if (result.terminal.type === 'turn_failed') error.value = result.terminal.message
    if (toolOutcomeUnknown.value) { status.value = 'review'; error.value = '运行轮次已结束，但有工具执行结果未知。请刷新运行账本并核对外部结果，勿直接重复执行。' }
  } catch (cause) {
    if (token !== revision) return
    if (active.signal.aborted) { status.value = 'unknown'; error.value = '连接已停止，执行状态尚未确认。请重新连接同一执行请求。' }
    else if (!reconnect && !restored.value && cause instanceof ExecutionError && cause.knownNotStarted && lastEventId.value === 0) { status.value = 'failed'; error.value = cause.message }
    else { status.value = 'unknown'; error.value = cause instanceof Error ? cause.message : '执行结果未知，请重新连接同一执行请求。' }
  } finally { if (token === revision) busy.value = false }
}
function label(event: ExecutionEvent) {
  if (event.type === 'turn_started') return `运行已启动 · 第 ${event.segment} 段`
  if (event.type === 'tool_intent') return `${event.tool_name} · ${event.decision_class} · Gate ${event.gate} · ${event.summary}`
  if (event.type === 'approval_requested') return `等待人工审批 ${event.approval_id} · 决策 ${event.decision_id}`
  if (event.type === 'tool_result') return `${event.decision_id} · ${event.status} · ${event.summary}`
  if (event.type === 'turn_completed') return `本轮结束 · 运行状态 ${event.run_state}`
  if (event.type === 'turn_failed') return `本轮未完成 · ${event.outcome === 'unknown' ? '结果未知' : '已确认失败'} · ${event.message}`
  return ''
}
watch(() => [props.runId, props.segment], () => {
  revision++; controller?.abort(); busy.value = false; status.value = 'idle'; events.value = []; output.value = ''; error.value = ''; approvalId.value = ''; requestId.value = ''; lastEventId.value = 0; turnId.value = ''
  outputTruncated.value = false; eventsTruncated.value = false
  toolOutcomeUnknown.value = false
  restored.value = false; executionRevision.value = ''; void loadState()
}, { immediate: true })
watch(() => props.locked, locked => { if (!locked && !executionRevision.value && !checking.value && !busy.value) void loadState() })
onScopeDispose(() => { revision++; controller?.abort(); emit('busy', false); emit('approval', null) })
</script>
<template>
  <section v-if="state !== 'closed'" class="detail-panel run-execution-panel" aria-label="Agent 运行控制">
    <div class="section-heading"><div><p class="eyebrow">AGENT EXECUTION / {{ runId }}</p><h2>Agent 运行</h2></div><span class="outline-label" role="status" aria-atomic="true">{{ statusLabel }}</span></div>
    
    <p class="muted">任务内容：{{ title }}</p>
    <p v-if="checking" role="status">正在查询服务端执行状态，不会自动启动任务或恢复执行连接。</p>
    <div v-else-if="stateError" class="approval-error approval-feedback" role="alert"><p>{{ stateError }}</p><button class="secondary" :disabled="busy || locked" @click="loadState">重新读取执行状态</button></div>
    <p v-else-if="status === 'idle' && executionRevision" class="muted">服务端确认此段尚未启动。运行登记只创建任务边界；此操作才会请求 Agent 开始处理。</p>
    <p v-if="restored" class="muted">已恢复服务端执行状态；此前输出未保存在本页。连接会从同一请求的事件起点续查，完整结果请查看运行账本。</p>
    <p v-if="busy" class="inline-feedback" role="status">{{ status === 'reconnecting' ? '连接中断，正在用同一请求标识续读；不会重复启动任务。' : approvalId ? `等待人工审批 ${approvalId}，执行流保持连接。` : status === 'connecting' ? '正在核验会话并连接运行流…' : '正在接收运行事件…' }}</p>
    <div v-if="error" class="approval-error approval-feedback" :role="status === 'unknown' || status === 'review' ? 'alert' : 'status'"><p>{{ error }}</p><button v-if="status === 'unknown'" class="secondary" :disabled="busy" @click="execute(true)">重新连接同一执行</button></div>
    <button v-if="approvalId" class="secondary" :disabled="!busy" @click="emit('approval', approvalId)">查看并处理此审批</button>
    <button v-if="canStart" class="primary" type="button" @click="execute()">启动 Agent 运行</button>
    <div v-if="output" class="execution-output"><h3>Agent 输出</h3><p class="muted">输出区可独立滚动；聚焦后使用方向键或 PageDown 阅读。</p><p v-if="outputTruncated" class="inspector-note" role="status">输出预览仅保留最近 50,000 个字符，较早内容已从本页预览移除；此预览不是完整账本或证据导出。</p><pre tabindex="0" role="region" aria-label="Agent 输出，可滚动阅读">{{ output }}</pre></div>
    <p v-if="eventsTruncated" class="inspector-note" role="status">本页仅展示最近 100 条运行事件；完整决策与证据请刷新运行账本核对。</p>
    <ol v-if="events.length" class="execution-events" aria-label="运行事件">
      <li v-for="event in events" :key="event.seq"><span class="event-sequence">{{ String(event.seq).padStart(3, '0') }}</span><span>{{ label(event) }}</span></li>
    </ol>
  </section>
</template>
