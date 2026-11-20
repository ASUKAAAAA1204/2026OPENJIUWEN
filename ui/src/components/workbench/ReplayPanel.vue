<script setup lang="ts">
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import type { DecisionRecord } from './audit'
import { parseReplay, replayBinding, type ReplayReport } from './replay'
import { writeSessionHeaders } from './writeSession'
const props = defineProps<{ runId: string; records: DecisionRecord[] }>()
const emit = defineEmits<{ refresh: []; busy: [value: boolean] }>()
const confirmed = ref(false), busy = ref(false), error = ref(''), stale = ref(false)
const report = shallowRef<ReplayReport | null>(null)
let revision = 0, controller: AbortController | undefined
watch(busy, value => emit('busy', value), { flush: 'sync' })
function reset() { revision++; controller?.abort(); report.value = null; confirmed.value = false; busy.value = false; error.value = ''; stale.value = false }
watch(() => [props.runId, props.records], reset)
onScopeDispose(reset)
const matched = computed(() => report.value?.items.filter(item => item.result === 'MATCH').length ?? 0)
const evaluable = computed(() => report.value?.items.filter(item => item.result !== 'UNREPRODUCIBLE').length ?? 0)
async function replay() {
  if (busy.value || stale.value || !confirmed.value || !props.records.length) return
  busy.value = true; error.value = ''; report.value = null
  const token = revision, records = props.records, runId = props.runId, active = new AbortController(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  try {
    const sessionHeaders = await writeSessionHeaders(active.signal)
    const requestId = crypto.randomUUID()
    const response = await fetch(`/api/v27/runs/${encodeURIComponent(runId)}/replay`, { signal: active.signal, credentials: 'same-origin', cache: 'no-store', method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...sessionHeaders, 'Idempotency-Key': requestId }, body: JSON.stringify({ request_id: requestId, expected_decisions: replayBinding(records), execute_tools: false }) })
    if (!response.ok) throw new Error(({ 401: '会话已失效。', 403: '当前账号没有回放权限。', 404: '回放服务尚未提供此接口。', 409: '账本快照已经变化。' } as Record<number, string>)[response.status] ?? '回放服务返回失败。')
    const result = parseReplay(await response.json(), runId, records)
    if (token === revision) report.value = result
  } catch (cause) { if (token === revision) { stale.value = true; error.value = `${active.signal.aborted ? '回放响应超时，结果未知。' : cause instanceof Error ? cause.message : '无法读取回放结果。'} 请刷新账本后重新核对，当前不计算一致率。` } }
  finally { clearTimeout(timer); if (token === revision) { busy.value = false; confirmed.value = false } }
}
</script>
<template>
  <section class="service-replay" aria-label="服务端规则回放">
    <h3>服务端回放</h3><p class="muted">按账本顺序重判，模型部分比较结构化字段。回放不执行原工具；缺证据仍保留在总分母内。结果证据需有引用和内容指纹；经过 Gate 的决策还需规则快照。</p>
    <label class="management-confirm"><input v-model="confirmed" type="checkbox" :disabled="busy || stale" />我已核对运行与证据快照，同意发起只读回放</label>
    <button class="secondary" :disabled="busy || stale || !confirmed || !records.length" @click="replay">{{ busy ? '正在执行回放…' : '发起服务端回放' }}</button>
    <p v-if="busy" role="status">正在获取规则重判结果，请勿重复提交。</p>
    <p v-if="error" role="alert" class="approval-error approval-feedback">{{ error }}<button class="text-action" @click="emit('refresh')">刷新运行账本</button></p>
    <div v-if="report" class="replay-result" aria-live="polite"><h3>回放完成 · {{ report.replay_id }}</h3><p>完成时间 {{ report.completed_at }} · 未执行工具</p><p>复现一致：{{ matched }} / {{ report.items.length }}（{{ report.items.length ? (matched / report.items.length * 100).toFixed(2) : '0' }}%）</p><p>可评估覆盖：{{ evaluable }} / {{ report.items.length }}；缺证据项保留在总决策分母内。</p><ul><li v-for="item in report.items" :key="item.decision_id"><strong>{{ item.decision_id }} · {{ item.result === 'MATCH' ? '一致' : item.result === 'MISMATCH' ? '不一致' : '不可复现' }}</strong><p>{{ item.reason }}</p></li></ul></div>
  </section>
</template>
