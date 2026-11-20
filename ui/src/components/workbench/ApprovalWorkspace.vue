<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useApprovals } from './approvals'
import { useQueueLocation } from './queueLocation'
import '../../styles/approval.css'
const props = defineProps<{ focusId?: string; executionRunId?: string; executionWaiting?: boolean }>()
const { mode, items, loading, submitting, error, stale, now, pendingCount, expired, load, answer } = useApprovals()
const emit = defineEmits<{ busy: [value: boolean]; returnExecution: [] }>()
watch(submitting, value => emit('busy', value), { flush: 'sync' })
onScopeDispose(() => emit('busy', false))
const filter = ref('待处理'), query = ref(''), selected = ref(''), note = ref(''), confirm = ref(false)
watch([filter, query, mode], () => { selected.value = '' }, { flush: 'sync' })
useQueueLocation('人工审批', mode, filter, selected, submitting)
watch(() => [props.focusId, props.executionRunId] as const, ([value, runId]) => { if (value) { mode.value = 'api'; filter.value = '全部'; query.value = `${runId ?? ''} ${value}`.trim(); selected.value = value } }, { immediate: true })
const filtered = computed(() => items.value.filter(item => {
  if (props.focusId) return item.approval_id === props.focusId && item.run_id === props.executionRunId
  const pending = item.status === 'pending' && !expired(item)
  return (filter.value === '全部' || (filter.value === '待处理' ? pending : !pending)) &&
    `${item.title} ${item.run_id} ${item.approval_id}`.toLowerCase().includes(query.value.trim().toLowerCase())
}))
const current = computed(() => selected.value ? filtered.value.find(item => item.approval_id === selected.value) : filtered.value[0])
const labels = { pending: '待人工复核', approved: '已批准', rejected: '已拒绝', timeout: '服务端已超时处置' }
const remaining = computed(() => current.value ? Math.max(0, Math.ceil((Date.parse(current.value.expires_at) - now.value) / 1000)) : 0)
function refresh() { note.value = ''; confirm.value = false; return load() }
watch(() => [current.value?.approval_id, current.value?.revision, mode.value], () => { note.value = ''; confirm.value = false; error.value = '' })
async function resolve(result: 'approve' | 'reject') {
  if (current.value && await answer(current.value, result, note.value, confirm.value)) filter.value = '已处理'
}
</script>
<template>
  <main class="task-workspace approval-workspace">
    <div class="workspace-heading"><div><p class="eyebrow">HUMAN REVIEW / 03</p><h1>人工审批</h1><p class="muted">先核对参数，再留下可追溯的决定。</p></div><span class="outline-label">{{ mode === 'preview' ? '演示队列' : '审批服务' }}</span></div>
    <div class="run-toolbar"><button class="secondary" :disabled="loading || submitting" @click="refresh">刷新队列</button></div>
    <p v-if="executionRunId && executionWaiting" class="inline-feedback" role="status">运行 {{ executionRunId }} 正在等待此审批；提交决定后，运行流将继续并记录工具结果。</p>
    <p v-else-if="executionRunId" class="muted">关联运行 {{ executionRunId }} 已离开审批等待状态。</p>
    <button v-if="executionRunId" class="secondary" :disabled="submitting" @click="emit('returnExecution')">{{ executionWaiting ? '返回运行执行详情' : '查看关联运行详情' }}</button>
    
    <div class="approval-overview"><span>等待复核<strong>{{ loading || (error && !items.length) ? '—' : pendingCount }}</strong></span><span>处理原则<strong>高危超时拒绝</strong></span><span>执行边界<strong>审批结果由执行端核验</strong></span></div>
    <div class="approval-filters"><button v-for="item in ['待处理', '已处理', '全部']" :key="item" :aria-pressed="filter === item" :disabled="submitting" @click="filter = item">{{ item }}</button></div>
    <label class="approval-search">搜索审批<input v-model="query" :disabled="submitting" type="search" placeholder="任务、运行或审批编号" /></label>
    <p v-if="error" class="approval-error approval-feedback" role="alert">{{ error }}<button v-if="stale" class="text-action" :disabled="submitting" @click="refresh">刷新并重新核对</button></p>
    <div v-if="loading" class="resource-state" role="status"><span class="loading-indicator" /><h2>正在读取审批队列</h2></div>
    <div v-else-if="error && !items.length" class="resource-state resource-error" role="status"><h2>无法读取审批队列</h2><p>请刷新重试；当前不展示任何审批结果。</p></div>
    <div v-else-if="props.focusId && !current" class="resource-state" role="status"><h2>未找到运行关联审批</h2><p>运行 {{ executionRunId }} · 审批 {{ focusId }}。请核对服务端审批队列。</p><button class="secondary" @click="refresh">刷新审批队列</button></div>
    <div v-else-if="!filtered.length" class="workspace-empty" role="status"><h2>{{ filter === '待处理' ? '没有待处理请求' : '没有匹配的审批' }}</h2><p class="muted">已到期请求在已处理与全部中保留。{{ error ? '读取失败不表示队列为空。' : '可调整筛选或刷新队列。' }}</p><button class="secondary" @click="filter = '全部'; query = ''">查看全部</button></div>
    <div v-else-if="filtered.length" class="approval-layout" :aria-busy="submitting">
      <section class="approval-queue" aria-label="审批列表"><button v-for="item in filtered" :key="item.approval_id" class="history-card" :aria-pressed="current?.approval_id === item.approval_id" :disabled="submitting" @click="selected = item.approval_id"><span class="history-number">{{ item.approval_id }}</span><strong>{{ item.title }}</strong><span class="history-meta">{{ expired(item) ? '已到期 · 待确认' : labels[item.status] }} · {{ item.risk === 'high' ? '高风险' : item.risk === 'mid' ? '中风险' : '低风险' }}</span></button></section>
      <div v-if="selected && !current" class="resource-state" role="status"><h2>所选审批当前不可见</h2><p>可能已到期、不在当前筛选中或不在账号可见范围内。不会自动打开其他请求。</p><button class="secondary" @click="selected = ''">清除审批定位</button></div>
      <article v-if="current" class="approval-card">
        <div class="section-heading"><span class="eyebrow">{{ current.approval_id }}</span><span class="approval-status" :data-status="current.status">{{ mode === 'preview' && current.status !== 'pending' ? '演示' : '' }}{{ expired(current) ? '已到期 · 待确认' : labels[current.status] }}</span></div>
        <h2>{{ current.title }}</h2><p class="muted">{{ current.tool_name }} · {{ current.risk === 'high' ? '高风险' : '受控操作' }}</p>
        <p class="approval-scope">{{ current.scope }}</p>
        <p v-if="current.status === 'pending' && !expired(current)" class="approval-deadline" role="timer">距截止 {{ remaining }} 秒 · 超时 {{ current.timeout_policy }}</p>
        <details class="approval-context"><summary>请求信息与审批范围</summary><dl class="approval-facts"><dt>关联运行</dt><dd>{{ current.run_id }}</dd><dt>关联决策</dt><dd>{{ current.decision_id }}</dd><dt>执行前判定</dt><dd>ASK · 转人工</dd><dt>操作范围</dt><dd>{{ current.scope }}</dd><dt>请求时间</dt><dd>{{ current.requested_at }}</dd><dt>截止时间</dt><dd>{{ current.expires_at }}</dd><dt>超时策略</dt><dd>{{ current.timeout_policy }}{{ current.risk === 'high' ? ' · 高危不得执行' : '' }}</dd><dt>请求版本</dt><dd>{{ current.revision }}</dd></dl></details>
        <h3>待核对参数摘要</h3><pre class="approval-params">{{ current.args_digest }}</pre>
        
        <template v-if="current.status === 'pending' && !expired(current)">
          <p v-if="!current.can_respond" class="approval-error">当前账号仅可查看，没有应答权限。</p>
          <label class="approval-note" for="approval-note">审批说明 <span>拒绝时必填</span></label><textarea id="approval-note" v-model="note" :disabled="submitting || stale || !current.can_respond" maxlength="2000" rows="3" placeholder="记录本次判断的依据…" />
          <label class="approval-check"><input v-model="confirm" :disabled="submitting || stale || !current.can_respond" type="checkbox" />我已核对目标、参数和操作范围</label>
          <div class="approval-actions"><button class="reject-button" :disabled="submitting || stale || !current.can_respond" @click="resolve('reject')">{{ mode === 'preview' ? '拒绝演示请求' : '拒绝请求' }}</button><button class="primary" :disabled="submitting || stale || !current.can_respond" @click="resolve('approve')">{{ submitting ? '正在提交…' : mode === 'preview' ? '批准演示请求' : '批准请求' }}</button></div>
        </template>
        <div v-else-if="expired(current)" class="replay-result" role="status"><h3>审批窗口已结束</h3><p>已停止应答。{{ mode === 'preview' ? '本地演示未执行工具，不生成真实超时凭据。' : '等待服务端确认超时处置；本机时钟仅用于界面保护。' }}</p><button class="secondary" @click="refresh">刷新处置结果</button></div>
        <div v-else-if="current.response" class="approval-result" role="status"><h3>{{ mode === 'preview' ? '演示' : '' }}{{ labels[current.status] }}</h3><p>{{ current.response.note }}</p><dl class="approval-facts"><dt>应答人</dt><dd>{{ current.response.approver_id }}</dd><dt>应答时间</dt><dd>{{ current.response.approve_ts }}</dd><dt>绑定实参摘要</dt><dd><pre class="approval-params">{{ current.response.approved_args_digest }}</pre></dd><dt>审批凭据指纹</dt><dd>{{ current.response.content_hash ?? '无真实指纹 · 演示结果' }}</dd></dl><p class="muted">{{ mode === 'preview' ? '结果仅在本次页面会话保留；未执行工具，未形成真实审批凭据。' : '审批已记录；工具执行结果需在运行账本中核查。' }}</p></div>
      </article>
    </div>
  </main>
</template>
