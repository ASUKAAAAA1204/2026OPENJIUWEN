<script setup lang="ts">
import { onScopeDispose, ref, shallowRef, watch } from 'vue'
import { lifecycleRequest, parseLifecycle, parseLifecycleReceipt, type LifecycleSnapshot } from './lifecycle'
const props = defineProps<{ runId: string; segment: number; state: string; locked?: boolean }>()
const emit = defineEmits<{ changed: [message: string]; refresh: []; busy: [value: boolean] }>()
const data = shallowRef<LifecycleSnapshot | null>(null), loading = ref(false), busy = ref(false), stale = ref(false), confirmed = ref(false), reason = ref(''), error = ref('')
let revision = 0, controller: AbortController | undefined
watch(busy, value => emit('busy', value), { flush: 'sync' })
async function load() {
  if (busy.value || props.locked) return
  const token = ++revision; controller?.abort(); const active = new AbortController(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  loading.value = true; data.value = null; stale.value = false; confirmed.value = false; reason.value = ''; error.value = ''
  try {
    const snapshot = parseLifecycle(await lifecycleRequest(`/api/v27/runs/${encodeURIComponent(props.runId)}/lifecycle`, active.signal), props.runId)
    if (token !== revision) return
    if (snapshot.run.segment !== props.segment || snapshot.run.state !== props.state) { stale.value = true; throw new Error('运行状态与当前账本快照不一致，请刷新运行账本后重新核对。') }
    data.value = snapshot
  } catch (cause) { if (token === revision) error.value = active.signal.aborted ? '运行状态读取超时，请重试。' : cause instanceof Error ? cause.message : '无法读取运行状态。' }
  finally { clearTimeout(timer); if (token === revision) loading.value = false }
}
async function submit(action: 'close' | 'resume') {
  if (!data.value || loading.value || busy.value || props.locked || stale.value) return
  const bound = data.value
  if (!(action === 'close' ? bound.can_close : bound.can_resume) || bound.run.state === 'closed') return
  error.value = ''
  if (!confirmed.value || !reason.value.trim()) { error.value = '请填写操作原因，并核对运行及当前段号。'; return }
  const token = revision, active = new AbortController(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  busy.value = true
  const requestId = crypto.randomUUID()
  try {
    const receipt = parseLifecycleReceipt(await lifecycleRequest(`/api/v27/runs/${encodeURIComponent(props.runId)}/${action}`, active.signal, { request_id: requestId, expected_revision: bound.revision, expected_segment: bound.run.segment, expected_state: bound.run.state, reason: reason.value.trim() }), action, requestId, bound)
    if (token !== revision) return
    data.value = receipt.snapshot
    emit('changed', action === 'close' ? `服务端已关闭运行，账本现在只读。审计 ${receipt.audit_id}。` : `服务端已恢复同一运行，第 ${receipt.snapshot.run.segment} 段已登记。未重复执行历史工具。审计 ${receipt.audit_id}。`)
  } catch (cause) { if (token === revision) { stale.value = true; error.value = active.signal.aborted ? '提交响应超时，操作结果未知。请刷新账本核对，勿重复提交。' : cause instanceof Error ? cause.message : '操作结果未知，请刷新账本核对。' } }
  finally { clearTimeout(timer); if (token === revision) { busy.value = false; confirmed.value = false } }
}
watch(() => [props.runId, props.segment, props.state], () => { revision++; controller?.abort(); busy.value = false; void load() }, { immediate: true })
onScopeDispose(() => { revision++; controller?.abort(); emit('busy', false) })
</script>
<template>
  <details class="detail-panel run-lifecycle-panel">
    <summary>运行生命周期与操作</summary>
    <p class="muted">同一任务保持运行标识。中断恢复只增加段号；关闭是正常结束，之后账本只读。取消页面等待不能撤销已提交操作。</p>
    <p v-if="locked" class="inline-feedback" role="status">Agent 轮次仍在执行或等待审批，当前不能并行关闭或恢复运行。</p>
    <p v-if="loading" role="status">正在读取运行状态与操作权限…</p>
    <div v-if="error" class="approval-error approval-feedback" role="alert"><p>{{ error }}</p><button class="text-action" :disabled="busy" @click="emit('refresh')">刷新账本并重新核对</button></div>
    <template v-if="data">
      <dl class="audit-facts"><dt>业务任务</dt><dd>{{ data.task_id }}</dd><dt>当前状态</dt><dd>{{ data.run.state }} · 第 {{ data.run.segment }} 段</dd><dt>Skill 版本</dt><dd>{{ data.skill_id }} / {{ data.skill_version }}</dd><dt>操作条件</dt><dd>{{ data.reason || '以服务端返回的操作权限为准。' }}</dd></dl>
      <p v-if="data.run.state === 'closed'" class="inline-feedback">此运行已关闭，不能继续写入或恢复。可查看、导出及回放已有账本。</p>
      <form v-else-if="data.can_close || data.can_resume" @submit.prevent>
        <fieldset :disabled="busy || locked || stale">
          <label>生命周期操作原因<textarea v-model="reason" maxlength="1000" rows="3" placeholder="说明关闭或中断恢复的原因" /></label>
          <label class="management-confirm"><input v-model="confirmed" type="checkbox" />我已核对当前运行、段号和操作影响</label>
          <div class="run-actions"><button v-if="data.can_resume" class="secondary" :disabled="!confirmed || !reason.trim()" type="button" @click="submit('resume')">{{ busy ? '正在提交…' : '恢复运行段' }}</button><button v-if="data.can_close" class="secondary" :disabled="!confirmed || !reason.trim()" type="button" @click="submit('close')">{{ busy ? '正在提交…' : '关闭运行' }}</button></div>
        </fieldset>
      </form>
      <p v-else class="inspector-note">当前没有可用的生命周期写操作。运行是否中断、是否仍有在途操作及权限由服务端判定。</p>
    </template>
    <button class="text-action" :disabled="busy || locked || loading || stale" @click="load">重新读取生命周期</button>
  </details>
</template>
