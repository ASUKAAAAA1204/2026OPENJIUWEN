<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { writeSessionHeaders } from './writeSession'
const props = defineProps<{ runId: string }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const confirmed = ref(false), busy = ref(false), error = ref(''), feedback = ref(''), auditId = ref('')
let controller: AbortController | undefined, revision = 0
watch(busy, value => emit('busy', value), { flush: 'sync' })
function reset() { revision++; controller?.abort(); confirmed.value = false; busy.value = false; error.value = ''; feedback.value = ''; auditId.value = '' }
watch(() => props.runId, reset)
onScopeDispose(reset)
async function download() {
  if (!confirmed.value || busy.value) return
  const token = revision, runId = props.runId, active = new AbortController(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  busy.value = true; error.value = ''; feedback.value = ''; auditId.value = ''
  try {
    const sessionHeaders = await writeSessionHeaders(active.signal)
    const requestId = crypto.randomUUID()
    const response = await fetch(`/api/v27/runs/${encodeURIComponent(runId)}/export`, { signal: active.signal, credentials: 'same-origin', cache: 'no-store', method: 'POST', headers: { Accept: 'application/x-ndjson', 'Content-Type': 'application/json', ...sessionHeaders, 'Idempotency-Key': requestId }, body: JSON.stringify({ request_id: requestId, format: 'jsonl' }) })
    if (!response.ok) throw new Error(({ 401: '登录已失效。', 403: '当前账号没有此运行的导出权限。', 404: '账本导出服务尚未提供。' } as Record<number, string>)[response.status] ?? '导出请求失败，请确认服务状态。')
    const expectedHash = response.headers.get('X-Content-SHA256'), exportedRun = response.headers.get('X-Run-Id'), audit = response.headers.get('X-Export-Audit-Id')
    if (exportedRun !== runId || !audit?.trim() || !expectedHash || !/^[a-f0-9]{64}$/.test(expectedHash) || !response.headers.get('Content-Type')?.startsWith('application/x-ndjson')) throw new Error('导出响应缺少运行绑定、审计或文件校验信息，已停止下载。')
    const bytes = await response.arrayBuffer()
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('')
    if (hash !== expectedHash) throw new Error('文件内容指纹不匹配，已停止下载。')
    const content = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    const lines = content.split(/\r?\n/).filter(line => line.trim())
    if (!lines.length) throw new Error('导出文件为空，已停止下载。')
    for (const line of lines) { const item = JSON.parse(line); if (typeof item !== 'object' || item === null || item.run_id !== runId) throw new Error('导出文件包含其他运行或无效记录，已停止下载。') }
    if (token !== revision) return
    const url = URL.createObjectURL(new Blob([bytes], { type: 'application/x-ndjson' }))
    try { const link = document.createElement('a'); link.href = url; link.download = `${runId.replace(/[^a-zA-Z0-9_-]/g, '_')}-ledger.jsonl`; link.click() }
    finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
    auditId.value = audit
    feedback.value = `已核对文件指纹并发起下载，共 ${lines.length} 条记录。保存结果请在浏览器下载列表确认。`
  } catch (cause) { if (token === revision) error.value = active.signal.aborted ? '导出超时，审计结果可能已登记；未发起文件下载。请核对服务状态后重试。' : cause instanceof Error ? cause.message : '文件校验或下载失败。' }
  finally { clearTimeout(timer); if (token === revision) { busy.value = false; confirmed.value = false } }
}
</script>
<template>
  <details class="detail-panel ledger-export"><summary>授权导出 JSONL</summary><p class="muted">导出由服务端按账号范围核验并登记审计；文件必须包含自包含账本，服务端负责脱敏。浏览器核对文件传输指纹，不代替账本哈希链验证。</p><label class="management-confirm"><input v-model="confirmed" type="checkbox" :disabled="busy" />我已确认此运行的导出范围和文件保存位置</label><button class="secondary" :disabled="busy || !confirmed" @click="download">{{ busy ? '正在准备并校验…' : '申请账本导出' }}</button><p v-if="busy" role="status">正在确认授权与文件内容，请勿重复提交。</p><p v-if="error" role="alert" class="approval-error approval-feedback">{{ error }}</p><p v-if="feedback" role="status" class="inline-feedback">{{ feedback }}<br />导出审计 {{ auditId }}</p></details>
</template>
