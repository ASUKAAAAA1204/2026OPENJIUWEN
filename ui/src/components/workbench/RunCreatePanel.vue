<script setup lang="ts">
import { computed, onMounted, onScopeDispose, reactive, ref, shallowRef, watch } from 'vue'
import { lifecycleRequest, parseLifecycleReceipt, parseRunContext, validateCreate, type RunContext, type CreateRunInput } from './lifecycle'
import type { RunPreview } from './model'
const emit = defineEmits<{ created: [run: RunPreview]; history: [taskId: string]; busy: [value: boolean] }>()
const context = shallowRef<RunContext | null>(null), loading = ref(false), busy = ref(false), stale = ref(false), confirmed = ref(false), error = ref(''), feedback = ref('')
watch(busy, value => emit('busy', value), { flush: 'sync' })
const result = shallowRef<RunPreview | null>(null)
const input = reactive<CreateRunInput>({ task_id: '', title: '', framework: 'openJiuwen', workspace_id: '', skill_id: '', skill_version: '' })
const skillKey = ref('')
const workspace = computed(() => context.value?.workspaces.find(item => item.id === input.workspace_id))
const prerequisites = computed(() => {
  if (!context.value) return []
  const reasons: string[] = []
  if (!context.value.workspaces.length) reasons.push('尚无授权工作目录，请联系管理员配置后刷新。')
  if (!context.value.skills.length) reasons.push('尚无可选 Skill 版本，请完成配置后刷新。')
  if (!context.value.frameworks.some(item => item.available)) reasons.push('三个框架均不可用，请先完成框架接入核验。')
  else if (!context.value.frameworks.some(item => item.name === input.framework && item.available)) reasons.push('当前所选框架不可用，请选择已开放的框架并重新核对任务范围。')
  return reasons
})
watch(() => [input.task_id, input.title, input.workspace_id, input.framework, skillKey.value], () => { confirmed.value = false }, { flush: 'sync' })
let revision = 0, controller: AbortController | undefined
async function load() {
  if (busy.value) return
  const token = ++revision; controller?.abort(); const active = new AbortController(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  loading.value = true; context.value = null; stale.value = false; confirmed.value = false; error.value = ''; result.value = null; feedback.value = ''
  try {
    const data = parseRunContext(await lifecycleRequest('/api/v27/run-context', active.signal))
    if (token !== revision) return
    context.value = data
    if (!data.workspaces.some(item => item.id === input.workspace_id)) input.workspace_id = ''
    if (!data.skills.some(item => JSON.stringify([item.id, item.version]) === skillKey.value)) skillKey.value = ''
  } catch (cause) { if (token === revision) error.value = active.signal.aborted ? '读取运行配置超时，请重试。' : cause instanceof Error ? cause.message : '无法读取运行配置。' }
  finally { clearTimeout(timer); if (token === revision) loading.value = false }
}
async function create() {
  if (busy.value || loading.value || stale.value || !context.value || result.value) return
  const skill = context.value.skills.find(item => JSON.stringify([item.id, item.version]) === skillKey.value)
  const bound = { ...input, task_id: input.task_id.trim(), title: input.title.trim(), skill_id: skill?.id ?? '', skill_version: skill?.version ?? '' }
  error.value = validateCreate(bound, context.value)
  if (!error.value && !confirmed.value) error.value = '请确认任务标识、工作目录与版本范围。'
  if (error.value) return
  const token = revision, active = new AbortController(); controller = active
  const timer = setTimeout(() => active.abort(), 12000)
  busy.value = true; feedback.value = ''
  const requestId = crypto.randomUUID()
  try {
    const receipt = parseLifecycleReceipt(await lifecycleRequest('/api/v27/runs/create', active.signal, { ...bound, expected_context_revision: context.value.revision, request_id: requestId }), 'create', requestId, bound)
    if (token !== revision) return
    result.value = receipt.snapshot.run
    feedback.value = `${receipt.created ? '运行已登记。' : '此业务任务已有运行，已返回原运行；未重复开账。'} 运行 ${receipt.snapshot.run.id} · 审计 ${receipt.audit_id}。登记本身不代表模型或工具已经执行。`
  } catch (cause) { if (token === revision) { stale.value = true; error.value = active.signal.aborted ? '提交响应超时，登记结果未知。请先在运行记录中按业务任务标识核对，勿直接重复创建。' : cause instanceof Error ? cause.message : '创建结果未知，请先查询运行记录。' } }
  finally { clearTimeout(timer); if (token === revision) { busy.value = false; confirmed.value = false } }
}
onMounted(load)
onScopeDispose(() => { revision++; controller?.abort(); emit('busy', false) })
</script>
<template>
  <section class="run-create-panel" aria-label="创建账本运行">
    <div class="section-heading"><h2>创建账本运行</h2><button class="secondary" :disabled="busy || loading" @click="load">刷新运行配置</button></div>
    <p class="muted">一个业务任务对应一个运行。使用稳定任务标识防止重复开账；登记后在详情中查看真实决策与证据。</p>
    <p v-if="loading" role="status">正在读取可用框架、Skill 与授权工作目录…</p>
    <div v-if="error" class="approval-error approval-feedback" role="alert"><p>{{ error }}</p><button v-if="stale" class="text-action" @click="emit('history', input.task_id.trim())">按任务查询运行记录</button></div>
    <div v-if="feedback" class="inline-feedback" role="status"><p>{{ feedback }}</p><button v-if="result" class="secondary" @click="emit('created', result)">查看已登记运行</button></div>
    <form v-if="context" class="run-create-form" @submit.prevent="create">
      <p v-if="!context.can_create" class="inspector-note">当前账号仅可查看配置，没有创建权限。</p>
      <div v-if="prerequisites.length" class="inline-feedback" role="status"><p><strong>创建条件尚未齐备</strong></p><ul><li v-for="item in prerequisites" :key="item">{{ item }}</li></ul></div>
      <fieldset :disabled="busy || stale || !context.can_create || !!result">
        <label>业务任务标识<input v-model="input.task_id" maxlength="160" autocomplete="off" placeholder="例如 ISSUE-128，同一任务继续使用此标识" required /></label>
        <label>任务描述<textarea v-model="input.title" maxlength="2000" rows="4" required placeholder="说明任务目标与预期结果" /></label>
        <label><span id="create-workspace-label">授权工作目录</span><select v-model="input.workspace_id" aria-labelledby="create-workspace-label" required><option value="" disabled>选择工作目录</option><option v-for="item in context.workspaces" :key="item.id" :value="item.id">{{ item.path }}</option></select></label>
        <p v-if="workspace" class="muted">文件与 Git 操作限定在 {{ workspace.path }}；命令仍需通过受控执行入口。</p>
        <label><span id="create-framework-label">账本运行框架</span><select v-model="input.framework" aria-labelledby="create-framework-label"><option v-for="item in context.frameworks" :key="item.name" :disabled="!item.available" :value="item.name">{{ item.name }} · {{ item.version }}{{ item.available ? '' : ' · 不可用' }}</option></select></label>
        <p class="muted">{{ context.frameworks.find(item => item.name === input.framework)?.reason }}</p>
        <label><span id="create-skill-label">Skill 与版本</span><select v-model="skillKey" aria-labelledby="create-skill-label" required><option value="" disabled>选择 Skill 版本</option><option v-for="item in context.skills" :key="JSON.stringify([item.id, item.version])" :value="JSON.stringify([item.id, item.version])">{{ item.id }} · {{ item.version }}</option></select></label>
        <label class="management-confirm"><input v-model="confirmed" type="checkbox" />我已核对任务标识、授权目录、框架和 Skill 版本</label>
        <p class="muted">修改任务、目录、框架或 Skill 版本后，需要重新确认范围。</p>
        <button class="primary" :disabled="!confirmed || prerequisites.length > 0" type="submit">{{ busy ? '正在登记运行…' : '创建账本运行' }}</button>
      </fieldset>
    </form>
    
  </section>
</template>
