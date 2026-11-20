<script setup lang="ts">
import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import { debtSummary, markLabels, remedies, runDebtSummary, useDebts } from './debts'
import { useQueueLocation } from './queueLocation'
import '../../styles/debt.css'
const { mode, data, loading, submitting, stale, error, load, resolve } = useDebts()
const emit = defineEmits<{ busy: [value: boolean] }>()
watch(submitting, value => emit('busy', value), { flush: 'sync' })
onScopeDispose(() => emit('busy', false))
const filter = ref('未消解'), query = ref(''), owner = ref('全部运行'), selected = ref(''), feedback = ref('')
watch([filter, query, owner, mode], () => { selected.value = '' }, { flush: 'sync' })
useQueueLocation('证据与债务', mode, filter, selected, submitting)
const form = reactive({ evidence_id: '', content_hash: '', regate_decision_id: '', confirmed: false })
const summary = computed(() => debtSummary(data.value?.items ?? []))
const owners = computed(() => data.value?.runs?.map(run => run.run_id) ?? [...new Set(data.value?.items.map(item => item.run_id) ?? [])])
const runReadings = computed(() => (data.value?.runs ?? []).map(run => ({ ...run, current: runDebtSummary(data.value?.items ?? [], data.value?.runs ?? [], run.run_id) })))
const queued = computed(() => runReadings.value.filter(run => run.review_status === 'queued').length)
const filtered = computed(() => (data.value?.items ?? []).filter(item =>
  (owner.value === '全部运行' || item.run_id === owner.value) && (filter.value === '全部' || (filter.value === '未消解' ? item.status === 'open' : item.status === 'resolved')) &&
  `${item.debt_id} ${item.decision_id} ${item.run_id} ${markLabels[item.mark_type]}`.toLowerCase().includes(query.value.trim().toLowerCase()),
))
const current = computed(() => selected.value ? filtered.value.find(item => item.debt_id === selected.value) : filtered.value[0])
function clearForm() { form.evidence_id = ''; form.content_hash = ''; form.regate_decision_id = ''; form.confirmed = false }
watch(() => [current.value?.debt_id, current.value?.status, mode.value], clearForm)
watch(() => [form.evidence_id, form.content_hash, form.regate_decision_id], () => { form.confirmed = false }, { flush: 'sync' })
function refresh() { clearForm(); feedback.value = ''; return load() }
async function submit() {
  if (!current.value) return
  const id = current.value.debt_id
  if (await resolve(current.value, { ...form })) {
    filter.value = '已消解'; selected.value = id
    feedback.value = mode.value === 'preview' ? '演示消解已登记，原始类型与权重保留；未写入真实账本。' : '服务端已返回消解记录，原始条目继续保留。'
  }
}
</script>
<template>
  <main class="task-workspace debt-workspace">
    <div class="workspace-heading"><div><p class="eyebrow">EVIDENCE &amp; DEBT / 04</p><h1>证据与债务</h1><p class="muted">看清缺口，补齐依据，保留每一步消解历史。</p></div><span class="outline-label">{{ mode === 'preview' ? '合成样例' : '债务服务' }}</span></div>
    <div class="run-toolbar"><button class="secondary" :disabled="loading || submitting" @click="refresh">刷新债务</button></div>
    
    <p v-if="error" class="approval-error approval-feedback" role="alert">{{ error }}<button v-if="stale" class="text-action" :disabled="submitting" @click="refresh">刷新并重新核对</button></p>
    <p v-if="feedback" class="inline-feedback" role="status">{{ feedback }}</p>
    <div v-if="loading" class="resource-state" role="status"><span class="loading-indicator" /><h2>正在读取债务与证据</h2></div>
    <div v-else-if="!data" class="resource-state resource-error"><h2>无法读取债务</h2><p>当前不展示估算债务，读取失败不能视为债务清零。</p></div>
    <template v-else>
      <div class="summary-grid"><div><span>未消解加权债务</span><strong>{{ summary.total }}</strong></div><div><span>未消解条目</span><strong>{{ summary.open }}</strong></div><div><span>原始条目保留</span><strong>{{ summary.retained }} / {{ summary.retained }}</strong></div></div>
      <details v-if="owners.length" class="debt-owners">
        <summary>分运行统计 · 按所有权去重<span v-if="data.runs"> · {{ queued }} 个运行在复核队列</span></summary>
        <template v-if="data.runs">
          <p class="muted">父级汇总包含自身与全部子运行；顶部总计只按原始所有权计算一次。复核阈值按运行自身债务判断，队列状态与结束读数来自快照，消解不会在本地擅自出队。</p>
          <ul><li v-for="run in runReadings" :key="run.run_id" class="debt-run-card">
            <div class="section-heading"><strong>{{ run.run_id }}</strong><span class="decision-tag">{{ run.review_status === 'queued' ? '复核队列中' : '未进入复核队列' }}</span></div>
            <p>{{ run.parent_run_id ? `父运行：${run.parent_run_id}` : '根运行' }}</p>
            <dl class="debt-run-metrics"><div><dt>自身债务</dt><dd>{{ run.current.own }}</dd></div><div><dt>子运行债务</dt><dd>{{ run.current.children }}</dd></div><div><dt>含子运行汇总</dt><dd>{{ run.current.total }}</dd></div><div><dt>自身复核阈值</dt><dd>{{ run.review_threshold }}</dd></div></dl>
            <p v-if="run.current.own >= run.review_threshold" class="debt-threshold">自身债务已达阈值{{ run.review_status !== 'queued' ? '；快照尚未确认入队，请刷新核对' : '' }}。</p>
            <p v-else>当前自身债务低于阈值{{ run.review_status === 'queued' ? '；已登记的复核仍待处理' : '' }}。</p>
            <p>{{ run.final_reading ? `结束时自身读数：债务 ${run.final_reading.total} · 未消解 ${run.final_reading.count} 条` : '结束读数尚未提供' }}</p>
          </li></ul>
        </template>
        <template v-else><p class="muted">服务未提供运行关系、复核阈值和结束读数；仅展示按所有权分列的当前债务，无法判定父子关系或复核状态。</p><ul><li v-for="id in owners" :key="id"><span>{{ id }}</span><strong>{{ debtSummary(data.items.filter(item => item.run_id === id)).total }}</strong><span>未消解权重</span></li></ul></template>
      </details>
      <div class="approval-filters"><button v-for="item in ['未消解', '已消解', '全部']" :key="item" :disabled="submitting" :aria-pressed="filter === item" @click="filter = item">{{ item }}</button></div>
      <div class="history-filters debt-filters"><label>搜索债务<input v-model="query" :disabled="submitting" type="search" placeholder="标记、运行、决策或债务编号" /></label><label><span id="debt-owner-label">所属运行</span><select v-model="owner" :disabled="submitting" aria-labelledby="debt-owner-label"><option>全部运行</option><option v-for="id in owners" :key="id">{{ id }}</option></select></label></div>
      <p class="muted">权重按原始等级计算：high 3、mid 2、low 1。影响呈现的衰减不减少债务。</p>
      <div class="debt-layout" :aria-busy="submitting">
        <section class="debt-list" aria-label="债务列表"><h2>债务条目 <span class="muted">{{ filtered.length }}</span></h2>
          <button v-for="item in filtered" :key="item.debt_id" class="history-card" :disabled="submitting" :aria-pressed="current?.debt_id === item.debt_id" @click="selected = item.debt_id"><span class="eyebrow">{{ item.mark_type }}</span><strong>{{ markLabels[item.mark_type] }}</strong><span class="history-meta">原始 {{ item.original_severity }} · 权重 {{ item.weight }} · {{ item.status === 'open' ? '未消解' : '已消解' }}</span><span class="history-number">{{ item.run_id }}</span></button>
          <div v-if="!filtered.length" class="workspace-empty" role="status"><h3>{{ data.items.length ? '当前筛选下没有条目' : '尚无债务记录' }}</h3><button class="secondary" @click="filter = '全部'; query = ''; owner = '全部运行'">清除筛选</button></div>
        </section>
        <div v-if="selected && !current" class="resource-state" role="status"><h2>所选债务当前不可见</h2><p>条目可能已消解、不在当前筛选中或不在账号可见范围内。不会自动打开其他条目。</p><button class="secondary" @click="selected = ''">清除债务定位</button></div>
        <section v-if="current" class="debt-detail" aria-label="债务详情">
          <div class="section-heading"><h2>{{ markLabels[current.mark_type] }}</h2><span class="decision-tag">{{ current.status === 'open' ? '待补充依据' : '已追加消解' }}</span></div>
          <dl class="audit-facts"><dt>债务标识</dt><dd>{{ current.debt_id }}</dd><dt>所属运行</dt><dd>{{ current.run_id }}</dd><dt>关联决策</dt><dd>{{ current.decision_id }}</dd><dt>原始等级 / 权重</dt><dd>{{ current.original_severity }} / {{ current.weight }}</dd><dt>下游影响呈现</dt><dd>{{ current.display_severity }} · 不改变原始权重</dd><dt>判据 / 原因</dt><dd>{{ current.cause ?? '未提供原因' }}</dd><dt>依赖来源</dt><dd>{{ current.provenance.length ? current.provenance.join('、') : current.cause === 'UNKNOWN_DEPENDENCY' ? '依赖未知，保留原始等级' : '未记录传播来源' }}</dd><dt>消解要求</dt><dd>{{ remedies[current.mark_type].label }}</dd></dl>
          <template v-if="current.status === 'open'">
            <div class="debt-remedy"><h3>{{ remedies[current.mark_type].label }}</h3><p v-if="current.mark_type === 'SOURCE_CONFLICT'">引用需指向统一来源的核验依据，不能仅补充任意文件。</p><p v-else-if="current.mark_type === 'SPEC_MISMATCH'">需修正参数并关联重新过闸记录，成功事实由服务端核验。</p><p v-else>提供缺失结果或输入快照的内容寻址引用，由服务端核验证据归属。</p></div>
            <p v-if="!data.can_resolve" class="approval-error">当前账号仅可查看债务，没有消解权限。</p>
            <form class="debt-form" @submit.prevent="submit">
              <label for="resolve-ref">{{ mode === 'preview' ? '演示证据标识' : '证据标识' }}</label><input id="resolve-ref" v-model="form.evidence_id" :disabled="submitting || stale || !data.can_resolve" maxlength="500" placeholder="evidence:…" />
              <label for="resolve-hash">内容指纹 SHA-256</label><input id="resolve-hash" v-model="form.content_hash" :disabled="submitting || stale || !data.can_resolve" maxlength="64" autocomplete="off" spellcheck="false" placeholder="64位小写十六进制" />
              <template v-if="current.mark_type === 'SPEC_MISMATCH'"><label for="regate-id">重新过闸决策标识</label><input id="regate-id" v-model="form.regate_decision_id" :disabled="submitting || stale || !data.can_resolve" placeholder="decision:…" /></template>
              <label class="approval-check"><input v-model="form.confirmed" :disabled="submitting || stale || !data.can_resolve" type="checkbox" />我已核对消解方法与证据范围</label>
              <button class="primary" :disabled="submitting || stale || !data.can_resolve">{{ submitting ? '正在提交…' : mode === 'preview' ? '登记演示消解' : '提交消解核验' }}</button>
            </form>
          </template>
          <section class="debt-history" aria-label="追加历史"><h3>追加历史 · 原始记录保留</h3><ol><li><span class="step-index">{{ current.opened_seq }}</span><div><strong>原始登记</strong><p>{{ current.mark_type }} · {{ current.original_severity }} · 权重 {{ current.weight }}</p></div></li><li v-if="current.resolved"><span class="step-index">{{ current.resolved.seq }}</span><div><strong>{{ mode === 'preview' ? '演示消解' : '追加消解' }}</strong><p>{{ remedies[current.mark_type].label }}</p><p>{{ current.resolved.evidence_ref.evidence_id }}</p><code>{{ current.resolved.evidence_ref.content_hash }}</code><p v-if="current.mark_type === 'SPEC_MISMATCH'">{{ mode === 'preview' ? '仅演示成功标记，未执行重新过闸' : '服务端返回重新过闸成功事实' }}</p></div></li></ol></section>
        </section>
      </div>
    </template>
  </main>
</template>
