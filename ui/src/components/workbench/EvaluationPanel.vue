<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { readEvaluationLocation, writeEvaluationLocation } from './evaluationLocation'
import { compareVersions, formatDelta, formatNumber, formatReading, readingDefinitions, useEvaluations, versionKey, type ReadingKey } from './evaluations'
import type { RunPreview } from './model'
import '../../styles/evaluation.css'
import { capabilityLabels, useManagement, type ManagementTarget } from './management'
defineProps<{ runs: RunPreview[] }>()
const emit = defineEmits<{ inspect: [run: RunPreview]; busy: [value: boolean]; lookup: [runId: string] }>()
const { mode, data, loading, error, load, versions } = useEvaluations()
const initialLocation = readEvaluationLocation(location.hash)
const selected = ref(initialLocation?.selected ?? ''), baselineKey = ref(initialLocation?.baseline ?? ''), candidateKey = ref(initialLocation?.candidate ?? ''), scope = ref(initialLocation?.scope ?? 'aggregate')
watch(mode, () => { selected.value = ''; scope.value = 'aggregate'; baselineKey.value = ''; candidateKey.value = '' }, { flush: 'sync' })
const current = computed(() => versions.value.find(item => versionKey(item) === selected.value))
const baseline = computed(() => versions.value.find(item => versionKey(item) === baselineKey.value))
const candidate = computed(() => versions.value.find(item => versionKey(item) === candidateKey.value))
const selectedRun = computed(() => current.value?.sample_runs.find(run => run.run_id === scope.value))
const managementTarget = computed<ManagementTarget | null>(() => current.value ? { skill_id: current.value.aggregate.skill_id, skill_version: current.value.aggregate.skill_version, policy_version: current.value.policy_version, evaluation_revision: data.value?.revision ?? '' } : mode.value === 'preview' ? { skill_id: 'DEMO-SKILL', skill_version: 'DEMO', policy_version: 'DEMO-POLICY', evaluation_revision: 'preview' } : null)
const manager = useManagement(mode, managementTarget)
watch(manager.submitting, value => emit('busy', value), { flush: 'sync' })
watch(mode, () => emit('busy', false), { flush: 'sync' })
const managementData = manager.data
const managementError = manager.error, managementFeedback = manager.feedback
const managementLoading = manager.loading, managementSubmitting = manager.submitting
const managementStale = manager.stale, releaseResult = manager.release, quoteResult = manager.quote
const configReason = ref(''), configConfirmed = ref(false), actionConfirmed = ref(false)
const comparisonReason = computed(() => compareVersions(baseline.value, candidate.value))
const comparisonAccess = computed(() => {
  if (managementLoading.value) return '正在核对版本对比权限。'
  if (!managementData.value || managementStale.value) return '版本对比权限尚未确认，请刷新管理状态。'
  if (!managementEnabled('version_comparison')) return '版本对比默认关闭。需具备管理权限的账号启用后再核对评估快照。'
  if (!managementCanExecute('version_comparison')) return '版本对比已启用，但当前账号未获执行授权。'
  if (!data.value?.comparison_enabled) return '配置已启用，当前评估快照尚未确认版本对比。请刷新评估后重新核对。'
  return ''
})
const comparisonStatus = computed(() => !managementData.value || managementStale.value ? '权限待确认' : !managementEnabled('version_comparison') ? '未启用' : !managementCanExecute('version_comparison') ? '未授权' : !data.value?.comparison_enabled ? '快照待刷新' : '已启用')
watch(data, value => {
  if (value && !selected.value && versions.value[0]) selected.value = versionKey(versions.value[0])
  configReason.value = ''; configConfirmed.value = false; actionConfirmed.value = false
})
watch(selected, () => { scope.value = 'aggregate'; configReason.value = ''; configConfirmed.value = false; actionConfirmed.value = false }, { flush: 'sync' })
const invalidLocation = computed(() => !!data.value && ((!!selected.value && !current.value) || (scope.value !== 'aggregate' && !selectedRun.value)))
function clearLocation() { selected.value = versions.value[0] ? versionKey(versions.value[0]) : ''; scope.value = 'aggregate'; baselineKey.value = ''; candidateKey.value = '' }
function restoreLocation() {
  if (managementSubmitting.value) return
  const value = readEvaluationLocation(location.hash)
  if (!value) return
  mode.value = 'api'
  selected.value = value.selected; scope.value = value.scope; baselineKey.value = value.baseline; candidateKey.value = value.candidate
}
function saveLocation() {
  if (new URLSearchParams(location.hash.slice(1)).get('section') !== '版本评估') return
  history.replaceState(null, '', `${location.pathname}${location.search}${writeEvaluationLocation({ source: mode.value, selected: selected.value, scope: scope.value, baseline: baselineKey.value, candidate: candidateKey.value })}`)
}
watch([mode, selected, scope, baselineKey, candidateKey], saveLocation, { flush: 'post' })
onMounted(() => { saveLocation(); window.addEventListener('popstate', restoreLocation); window.addEventListener('workspace-location', restoreLocation) })
onUnmounted(() => { window.removeEventListener('popstate', restoreLocation); window.removeEventListener('workspace-location', restoreLocation) })
watch([() => manager.data.value?.revision, managementLoading], () => { configReason.value = ''; configConfirmed.value = false; actionConfirmed.value = false })
function label(item: typeof versions.value[number]) { return `${item.aggregate.skill_id} · ${item.aggregate.skill_version} · ${item.policy_version}` }
function value(key: ReadingKey) {
  if (!selectedRun.value) return formatReading(current.value?.aggregate.aggregated_readings, key)
  const readings = selectedRun.value.readings
  if (key === 'uncertainty_debt') return formatNumber(readings.uncertainty_debt.weighted_value)
  return readings[key].value === null ? '不适用' : `${formatNumber(readings[key].value! * 100)}%`
}
function detail(key: ReadingKey) {
  if (!current.value) return '尚无可复算账本'
  if (selectedRun.value) {
    if (key === 'uncertainty_debt') return `未消解 ${selectedRun.value.readings.uncertainty_debt.unresolved_count} 条 · 无分母`
    const rate = selectedRun.value.readings[key]
    return rate.denominator === 0 ? '分母为 0，不参与聚合' : `分子 ${rate.numerator} / 分母 ${rate.denominator}`
  }
  const readings = current.value.aggregate.aggregated_readings
  if (!readings) return '样本不足，暂不聚合'
  if (key === 'uncertainty_debt') return `每运行平均未消解 ${formatNumber(readings.uncertainty_debt.unresolved_count)} 条`
  return `等权平均 · ${readings[key].runs_considered} 次适用运行`
}
const coverage = computed(() => {
  const rate = selectedRun.value?.readings.reproducibility.evaluable_coverage ?? current.value?.aggregate.aggregated_readings?.reproducibility.evaluable_coverage
  return rate?.value === null || !rate ? 'N/A' : `${formatNumber(rate.value * 100)}%`
})
function configure(key: 'release_gate' | 'remediation_quote' | 'version_comparison', enabled: boolean) {
  return manager.mutate('configure', key, enabled, configReason.value, configConfirmed.value)
}
function runManagement(action: 'release' | 'quote') { return manager.mutate(action, undefined, undefined, '', actionConfirmed.value) }
function money(amountMinor: number) { return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(amountMinor / 100) }
function managementEnabled(key: 'release_gate' | 'remediation_quote' | 'version_comparison') { return !!managementData.value?.capabilities[key].enabled }
function managementCanConfigure(key: 'release_gate' | 'remediation_quote' | 'version_comparison') { return !!managementData.value?.capabilities[key].can_configure }
function managementCanExecute(key: 'release_gate' | 'remediation_quote' | 'version_comparison') { return !!managementData.value?.capabilities[key].can_execute }
</script>
<template>
  <div class="evaluation-panel">
    <div class="run-toolbar">
      
      <button class="secondary" :disabled="loading || managementSubmitting" @click="load">刷新评估</button>
    </div>
    
    
    <p v-if="mode === 'api' && data" class="evaluation-provenance muted">快照 {{ data.revision }} · 生成时间 {{ data.generated_at }}</p>
    <div v-if="loading" class="resource-state" role="status"><span class="loading-indicator" /><h2>正在读取版本与运行样本</h2></div>
    <div v-else-if="error" class="resource-state resource-error" role="alert"><h2>无法读取版本评估</h2><p>{{ error }}</p><button class="secondary" @click="load">重新读取</button></div>
    <div v-else-if="invalidLocation" class="resource-state" role="status"><h2>所选版本或运行范围当前不可见</h2><p>可能不在最新样本窗口或账号可见范围内。当前不会用其他版本或聚合读数替代，也不会恢复发布确认。</p><button class="secondary" @click="clearLocation">清除评估定位</button></div>
    <template v-else-if="data">
      <div v-if="versions.length" class="history-filters evaluation-selectors">
        <label>Skill 与版本<select v-model="selected" :disabled="managementSubmitting"><option v-for="version in versions" :key="versionKey(version)" :value="versionKey(version)">{{ label(version) }}</option></select></label>
        <label>读数范围<select v-model="scope"><option value="aggregate">Skill 聚合 · 最近20次有效运行</option><option v-for="run in current?.sample_runs ?? []" :key="run.run_id" :value="run.run_id">单次运行 · {{ run.run_id }}</option></select></label>
      </div>
      <p v-if="mode === 'api' && !versions.length" class="inline-feedback" role="status">暂无可评估版本。完成包含白名单决策的运行后，刷新此页。</p>
      <div class="summary-grid">
        <div><span>有效运行样本</span><strong>{{ current?.aggregate.sample_run_count ?? 0 }} / 5</strong></div>
        <div><span>决策类覆盖</span><strong>{{ current?.aggregate.class_count ?? 0 }} / 3</strong></div>
        <div><span>发布闸</span><strong class="summary-text">{{ managementData ? managementData.capabilities.release_gate.enabled ? '已启用' : '未启用' : managementLoading ? '读取中' : '未知' }}</strong></div>
      </div>
      <p v-if="current" class="evaluation-provenance muted">{{ current.aggregate.skill_id }} / {{ current.aggregate.skill_version }} · 策略 {{ current.policy_version }} · 白名单 {{ current.whitelist_version }} · 读数 {{ current.aggregate.schema_version }}</p>
      <div v-if="selectedRun" class="inline-feedback sample-trace">
        <p>当前读数来自运行 {{ selectedRun.run_id }}。查看原始决策与证据时会重新查询授权运行记录。</p>
        <button class="secondary" :disabled="managementSubmitting" @click="emit('lookup', selectedRun.run_id)">查找运行账本</button>
      </div>
      <div class="section-heading"><h2>{{ selectedRun ? '运行读数' : 'Skill 聚合读数' }}</h2><span class="outline-label">{{ selectedRun ? selectedRun.run_id : current?.aggregate.status === 'ok' ? '样本满足要求' : '样本不足' }}</span></div>
      <div class="metrics-grid">
        <div v-for="metric in readingDefinitions" :key="metric.key">
          <span>{{ metric.name }}</span><strong>{{ value(metric.key) }}</strong><small>{{ detail(metric.key) }}</small>
          <small v-if="metric.key === 'reproducibility'">可评估覆盖率 {{ coverage }}</small>
          <details class="reading-explanation"><summary>计算口径</summary><p>{{ metric.formula }}</p><p>{{ metric.note }}</p></details>
        </div>
      </div>
      <section class="detail-panel">
        <h2>样本与聚合依据</h2>
        <p class="muted">最近 20 次有效运行，等权平均；至少 5 次运行且覆盖至少 3 个决策类后，才展示 Skill 聚合读数。</p>
        <details class="reading-explanation"><summary>哪些运行可以计入样本？</summary><p>至少包含 1 个白名单决策的运行才有效。决策按标识去重并计入所属运行；无自身决策的纯编排运行不计入样本。</p><p>比例读数分母为 0 时记为不适用，不参与聚合。不确定债务展示每运行的平均加权值与平均未消解条目数。当前会话预览不计入真实样本。</p></details>
        <details v-if="current?.sample_runs.length" class="reading-explanation"><summary>查看纳入的 {{ current.aggregate.sample_run_count }} 次运行</summary><ol class="evaluation-samples"><li v-for="run in current.sample_runs" :key="run.run_id"><button class="text-action" @click="scope = run.run_id">{{ run.run_id }}</button><span>{{ new Date(run.completed_at).toLocaleString('zh-CN', { hour12: false }) }} · {{ run.whitelist_decision_count }} 个白名单决策</span></li></ol></details>
      </section>
      <section class="detail-panel evaluation-comparison">
        <div class="section-heading"><h2>版本对比</h2><span class="outline-label">{{ comparisonStatus }}</span></div>
        <div v-if="comparisonAccess" class="inline-feedback" role="status"><p>{{ comparisonAccess }}</p><button v-if="managementEnabled('version_comparison') && managementCanExecute('version_comparison') && !data.comparison_enabled" class="secondary" :disabled="loading || managementSubmitting" @click="load">刷新评估快照</button></div>
        <template v-else>
          <div class="history-filters evaluation-selectors"><label>基准版本<select v-model="baselineKey"><option value="">选择基准版本</option><option v-for="version in versions" :key="versionKey(version)" :value="versionKey(version)">{{ label(version) }}</option></select></label><label>候选版本<select v-model="candidateKey"><option value="">选择候选版本</option><option v-for="version in versions" :key="versionKey(version)" :value="versionKey(version)">{{ label(version) }}</option></select></label></div>
          <p v-if="comparisonReason" class="inline-feedback" role="status">{{ comparisonReason }}</p>
          <template v-else-if="baseline?.aggregate.aggregated_readings && candidate?.aggregate.aggregated_readings">
            <p class="muted">候选减基准。两侧均按同一读数、策略与白名单版本计算；差值不自动代表质量提升或发布通过。</p>
            <p>基准 {{ baseline.aggregate.sample_run_count }} 次 / {{ baseline.aggregate.class_count }} 类 · 候选 {{ candidate.aggregate.sample_run_count }} 次 / {{ candidate.aggregate.class_count }} 类</p>
            <div class="evaluation-table-scroll" tabindex="0" role="region" aria-label="版本读数对比"><table class="evaluation-table"><caption>八项读数差 · 候选减基准</caption><thead><tr><th scope="col">读数</th><th scope="col">基准</th><th scope="col">候选</th><th scope="col">差值</th></tr></thead><tbody><tr v-for="metric in readingDefinitions" :key="metric.key"><th scope="row">{{ metric.name }}</th><td data-label="基准">{{ formatReading(baseline.aggregate.aggregated_readings, metric.key) }}<small v-if="metric.key === 'uncertainty_debt'">平均 {{ formatNumber(baseline.aggregate.aggregated_readings.uncertainty_debt.unresolved_count) }} 条</small></td><td data-label="候选">{{ formatReading(candidate.aggregate.aggregated_readings, metric.key) }}<small v-if="metric.key === 'uncertainty_debt'">平均 {{ formatNumber(candidate.aggregate.aggregated_readings.uncertainty_debt.unresolved_count) }} 条</small></td><td data-label="差值">{{ formatDelta(baseline.aggregate.aggregated_readings, candidate.aggregate.aggregated_readings, metric.key) }}</td></tr></tbody></table></div>
          </template>
        </template>
      </section>
      <section class="detail-panel evaluation-management">
        <div class="section-heading"><h2>发布与整改管理</h2><button class="secondary" :disabled="managementLoading || managementSubmitting" @click="manager.load">刷新管理状态</button></div>
        
        <p v-if="mode === 'api'" class="muted">{{ managementData ? `服务端配置修订 ${managementData.revision}` : '管理服务状态' }} · 每项变更需服务端授权并留下审计记录。</p>
        <div v-if="managementLoading" class="resource-state" role="status"><span class="loading-indicator" /><h3>正在读取管理权限</h3></div>
        <div v-else-if="managementError" class="resource-state resource-error" role="alert"><h3>管理服务不可用</h3><p>{{ managementError }}</p><button class="secondary" :disabled="managementSubmitting" @click="manager.load">刷新并确认状态</button></div>
        <template v-else-if="managementData">
          <p v-if="managementStale" class="inline-feedback" role="alert">{{ managementError }}<button class="text-action" :disabled="managementSubmitting" @click="manager.load">重新读取</button></p>
          <p v-if="managementFeedback" class="inline-feedback" role="status">{{ managementFeedback }}</p>
          <dl class="management-capabilities">
            <template v-for="key in ['release_gate', 'remediation_quote', 'version_comparison'] as const" :key="key">
              <dt>{{ capabilityLabels[key] }}</dt>
              <dd><strong>{{ managementEnabled(key) ? '已启用' : '未启用' }}</strong><span>管理权限：{{ managementCanConfigure(key) ? '可变更' : '只读' }}</span><span>执行权限：{{ managementCanExecute(key) ? '允许' : '未授权' }}</span></dd>
            </template>
          </dl>
          <details v-if="managementData.last_change" class="reading-explanation"><summary>最近一次配置审计</summary><p>操作人 {{ managementData.last_change.actor_id }} · {{ new Date(managementData.last_change.at).toLocaleString('zh-CN', { hour12: false }) }}</p><p>原因：{{ managementData.last_change.reason }} · 审计标识 {{ managementData.last_change.audit_id }}</p></details>
          <form class="management-form" @submit.prevent="configure('version_comparison', !managementEnabled('version_comparison'))">
            <label>变更原因<input v-model="configReason" :disabled="managementSubmitting || managementStale" required maxlength="300" placeholder="记录启用/停用依据" /></label>
            <label class="management-confirm"><input v-model="configConfirmed" type="checkbox" :disabled="managementSubmitting || managementStale" />我已核对版本、策略与变更影响。</label>
            <button v-for="key in ['release_gate', 'remediation_quote'] as const" :key="key" type="button" class="secondary" :disabled="!managementCanConfigure(key) || managementSubmitting || managementStale" @click="configure(key, !managementEnabled(key))">{{ managementEnabled(key) ? '停用' : '启用' }}{{ capabilityLabels[key] }}</button><button class="secondary" :disabled="!managementCanConfigure('version_comparison') || managementSubmitting || managementStale">{{ managementEnabled('version_comparison') ? '停用' : '启用' }}版本对比</button>
          </form>
          <p class="muted">发布闸与整改报价的启停由具备管理权限的账号逐项变更；以下操作还会按当前版本与评估快照重新向服务端核验。</p>
          <div class="management-checks"><span>shadow 样本</span><strong>{{ releaseResult ? releaseResult.checks.find(check => check.name === 'shadow 样本')?.actual ?? '评估结果未包含' : '待服务端评估' }}</strong><span>误报率</span><strong>{{ releaseResult ? releaseResult.checks.find(check => check.name === 'shadow 误报率')?.actual ?? '评估结果未包含' : '待服务端评估' }}</strong><span>实时拦截上线条件</span><strong>至少 100 次运行，误报率不高于 5%</strong></div>
          <label class="management-confirm"><input v-model="actionConfirmed" type="checkbox" :disabled="managementSubmitting || managementStale" />我已核对所选版本、策略和当前评估快照。</label>
          <div class="management-actions"><button class="new-task" :disabled="!managementEnabled('release_gate') || !managementCanExecute('release_gate') || !managementTarget || !actionConfirmed || managementSubmitting || managementStale" @click="runManagement('release')">{{ managementSubmitting ? '正在重新评估…' : '重新核验发布条件' }}</button><button class="secondary" :disabled="!managementEnabled('remediation_quote') || !managementCanExecute('remediation_quote') || !managementTarget || !actionConfirmed || managementSubmitting || managementStale" @click="runManagement('quote')">{{ managementSubmitting ? '正在核算…' : '获取逐条整改报价' }}</button></div>
          <section v-if="releaseResult" class="management-result" :class="`verdict-${releaseResult.verdict.toLowerCase()}`" aria-live="polite"><h3>发布评估：{{ releaseResult.verdict === 'PASS' ? '通过条件核验' : '阻断' }}</h3><p>评估 {{ releaseResult.assessment_id }} · {{ new Date(releaseResult.assessed_at).toLocaleString('zh-CN', { hour12: false }) }} · 未执行发布</p><ul><li v-for="reason in releaseResult.reasons" :key="reason">{{ reason }}</li></ul><dl><template v-for="check in releaseResult.checks" :key="check.name"><dt>{{ check.name }} · {{ check.passed ? '满足' : '未满足' }}</dt><dd>{{ check.actual }} / 要求 {{ check.required }}</dd></template></dl></section>
          <section v-if="quoteResult" class="management-result"><h3>整改估价单 {{ quoteResult.quote_id }}</h3><p>{{ new Date(quoteResult.created_at).toLocaleString('zh-CN', { hour12: false }) }} · {{ mode === 'preview' ? '合成示例，无真实计价依据' : '价格依据由服务端返回' }} · 未创建订单</p><div v-if="quoteResult.items.length" class="evaluation-table-scroll" tabindex="0" role="region" aria-label="逐条整改估价，可滚动阅读"><table class="evaluation-table"><caption>逐条估算与依据</caption><thead><tr><th scope="col">决策</th><th scope="col">整改项</th><th scope="col">依据</th><th scope="col">金额</th></tr></thead><tbody><tr v-for="item in quoteResult.items" :key="item.item_id"><th scope="row">{{ item.decision_id }}</th><td data-label="整改项">{{ item.action }}</td><td data-label="依据">{{ item.basis }}</td><td data-label="金额">{{ money(item.amount_minor) }}</td></tr></tbody></table></div><strong>估价合计 {{ money(quoteResult.total_minor) }}</strong></section>
        </template>
      </section>
      <section v-if="mode === 'preview' && runs.length" class="detail-panel"><h2>会话任务入口</h2><button v-for="run in runs" :key="run.id" class="history-card" @click="emit('inspect', run)"><strong>{{ run.title }}</strong><span class="history-meta">{{ run.id }} · 仅演示，未计入样本 ↗</span></button></section>
    </template>
  </div>
</template>
