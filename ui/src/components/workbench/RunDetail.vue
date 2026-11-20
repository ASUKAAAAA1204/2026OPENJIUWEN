<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onScopeDispose, ref, toRef, watch } from 'vue'
import type { RunPreview } from './model'
import { buildPreviewExport, replayCoverage } from './audit'
import { useAuditSource, type AuditMode } from './auditSource'
import ReplayPanel from './ReplayPanel.vue'
import LedgerExport from './LedgerExport.vue'
import EventTimeline from './EventTimeline.vue'
import RunLifecycle from './RunLifecycle.vue'
import RunRelations from './RunRelations.vue'
import RunClaims from './RunClaims.vue'
import { navigateView } from './viewNavigation'
import { decisionViews as detailViews, readDecisionLocation, writeDecisionLocation } from './runLocation'
import DecisionClasses from './DecisionClasses.vue'
import DecisionRisks from './DecisionRisks.vue'
import { decisionClassKey } from './decisionClasses'
import { relatedRun } from './runs'
const RunExecutionPanel = defineAsyncComponent(() => import('./RunExecutionPanel.vue'))

const props = defineProps<{ run: RunPreview }>()
const emit = defineEmits<{ back: []; compose: [run: RunPreview]; related: [run: RunPreview]; busy: [value: boolean]; approval: [id: string | null] }>()
const query = ref('')
const gate = ref('全部判定')
const decisionClass = ref('')
const riskStatus = ref('')
const initialDecision = readDecisionLocation(location.hash, props.run.id)
const selected = ref(initialDecision.decision)
const tab = ref(initialDecision.view)
const feedback = ref('')
const lifecycleBusy = ref(false), exportBusy = ref(false), replayBusy = ref(false), executionBusy = ref(false), refreshPending = ref(false)
const operationBusy = computed(() => lifecycleBusy.value || exportBusy.value || replayBusy.value || executionBusy.value)
watch(operationBusy, value => emit('busy', value), { flush: 'sync' })
onScopeDispose(() => emit('busy', false))
const replayed = ref(false)
const mode = ref<AuditMode>('api')
const { data, state, error, reload } = useAuditSource(toRef(props, 'run'), mode)
const executionAccessGeneration = ref(0)
watch(state, value => {
  if (['unauthorized', 'forbidden', 'not-found'].includes(value)) executionAccessGeneration.value++
}, { flush: 'sync' })
function refreshAudit() {
  if (operationBusy.value || state.value === 'loading') return
  refreshPending.value = false
  void reload()
}
function lifecycleChanged(message: string) { feedback.value = message; refreshPending.value = true }
watch([operationBusy, refreshPending], ([busy, pending]) => { if (!busy && pending) refreshAudit() })
const records = computed(() => data.value?.decisions ?? [])
const isPreview = computed(() => data.value?.source !== 'ledger')
const filtered = computed(() => records.value.filter(item =>
  (!riskStatus.value || item.status === riskStatus.value) &&
  (!decisionClass.value || decisionClassKey(item.class) === decisionClass.value) &&
  (gate.value === '全部判定' || item.gate === gate.value) &&
  `${item.decision_id} ${item.tool_name} ${item.class}`.toLowerCase().includes(query.value.trim().toLowerCase()),
))
const missingSelected = computed(() => !!selected.value && !records.value.some(item => item.decision_id === selected.value))
const current = computed(() => selected.value ? filtered.value.find(item => item.decision_id === selected.value) : filtered.value[0])
const hiddenSelected = computed(() => !!selected.value && !missingSelected.value && !current.value)
function persistDecisionLocation() {
  if (isPreview.value) return
  window.history.replaceState(null, '', `${location.pathname}${location.search}${writeDecisionLocation(props.run, selected.value, tab.value)}`)
}
function selectDecision(id: string) { selected.value = id; persistDecisionLocation() }
function selectView(view: string) { tab.value = view; if (current.value) selected.value = current.value.decision_id; persistDecisionLocation() }
function restoreDecisionLocation() {
  if (operationBusy.value || new URLSearchParams(location.hash.slice(1)).get('run') !== props.run.id) return
  const restored = readDecisionLocation(location.hash, props.run.id)
  selected.value = restored.decision; tab.value = restored.view
  query.value = ''; gate.value = '全部判定'; decisionClass.value = ''; riskStatus.value = ''
}
window.addEventListener('popstate', restoreDecisionLocation)
window.addEventListener('workspace-location', restoreDecisionLocation)
onScopeDispose(() => { window.removeEventListener('popstate', restoreDecisionLocation); window.removeEventListener('workspace-location', restoreDecisionLocation) })
const coverage = computed(() => replayCoverage(records.value))
watch(() => props.run.id, () => {
  refreshPending.value = false
  if (props.run.source === 'ledger') mode.value = 'api'
  selected.value = ''; query.value = ''; gate.value = '全部判定'; decisionClass.value = ''; riskStatus.value = ''; replayed.value = false; feedback.value = ''
  restoreDecisionLocation()
})
watch(mode, () => { refreshPending.value = false; replayed.value = false; feedback.value = ''; selected.value = ''; decisionClass.value = ''; riskStatus.value = ''; tab.value = detailViews[0]; if (mode.value === 'api') restoreDecisionLocation() })
function locateRisk(status: string) {
  if (replayBusy.value) return
  query.value = ''; gate.value = '全部判定'; decisionClass.value = ''; riskStatus.value = status
  selected.value = ''; persistDecisionLocation()
  requestAnimationFrame(() => document.getElementById('decision-risk-filter')?.focus())
}
async function locateClaimDecision(id: string) {
  if (replayBusy.value) return
  if (!records.value.some(item => item.decision_id === id)) return
  query.value = ''; gate.value = '全部判定'; decisionClass.value = ''; riskStatus.value = ''
  selected.value = id; tab.value = '判定与参数'; replayed.value = false
  persistDecisionLocation()
  await nextTick()
  document.getElementById('selected-decision-heading')?.focus()
}
function exportPreview() {
  if (!isPreview.value) return
  try {
    const blob = new Blob([JSON.stringify(buildPreviewExport(props.run), null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${props.run.id.replace(/[^a-zA-Z0-9_-]/g, '_')}-preview.json`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    feedback.value = '已生成预览文件；文件中明确标注非真实账本。'
  } catch {
    feedback.value = '无法生成下载文件，请检查浏览器下载权限后重试。'
  }
}
</script>
<template>
  <main class="task-workspace run-detail">
    <button class="text-action" @click="$emit('back')">← 返回运行记录</button>
    <div class="workspace-heading">
      <div>
        <p class="eyebrow">RUN DETAIL / {{ run.id }}</p>
        <h1>运行详情</h1>
        <p class="muted">{{ run.title }}</p>
      </div>
      <span class="outline-label">{{ mode === 'preview' ? '界面预览 · 未执行' : '账本服务' }}</span>
    </div>
    <div class="run-toolbar">
      
      <div class="run-actions">
        <button v-if="mode === 'api'" class="secondary" :disabled="operationBusy || state === 'loading'" @click="refreshAudit">{{ state === 'loading' ? '正在刷新账本…' : '刷新账本' }}</button>
        <button v-if="run.source !== 'ledger'" class="secondary" @click="$emit('compose', run)">回到任务</button>
        <button v-if="mode === 'preview'" class="secondary" :disabled="state === 'loading'" @click="exportPreview">下载预览 JSON</button>
      </div>
    </div>
    <p v-if="operationBusy" class="muted" role="status">当前操作正在提交或校验，完成后可刷新账本；刷新不会撤销服务端操作。</p>
    
    <KeepAlive :key="executionAccessGeneration" :max="1">
      <RunExecutionPanel v-if="data?.source === 'ledger' && ['ready', 'empty'].includes(state)" :key="`${run.id}-${data.segment}`" :run-id="run.id" :title="run.title" :segment="data.segment" :state="data.state" :locked="lifecycleBusy || exportBusy || replayBusy" @busy="executionBusy = $event" @approval="emit('approval', $event)" />
    </KeepAlive>
    <div v-if="state === 'loading'" class="resource-state" role="status" aria-live="polite"><span class="loading-indicator" /><h2>正在读取运行账本</h2><p>正在获取决策与证据记录…</p></div>
    <div v-else-if="error" class="resource-state resource-error" role="alert"><span class="eyebrow">{{ state.toUpperCase() }}</span><h2>{{ state === 'forbidden' ? '访问受限' : state === 'unauthorized' ? '需要重新登录' : state === 'not-found' ? '未找到运行账本' : '暂时无法加载' }}</h2><p>{{ error }}</p><button class="secondary" @click="reload">重新读取</button></div>
    <div v-else-if="state === 'empty'" class="resource-state" role="status"><h2>此运行暂无决策记录</h2><p>运行可能尚未触发工具调用。空记录不表示证据完整或回放通过。</p><button class="secondary" @click="reload">刷新记录</button></div>
    <template v-if="data && ['ready', 'empty'].includes(state)">
    <p class="muted run-lifecycle">{{ run.framework }} · segment {{ data.segment }} · {{ data.state === 'preview' ? '尚无真实运行生命周期' : data.state }}</p>
    <LedgerExport v-if="!isPreview" :key="run.id" :run-id="run.id" @busy="exportBusy = $event" />
    <RunLifecycle v-if="!isPreview" :key="`${run.id}-${data.segment}-${data.state}`" :run-id="run.id" :segment="data.segment" :state="data.state" :locked="executionBusy" @busy="lifecycleBusy = $event" @refresh="refreshAudit" @changed="lifecycleChanged" />
    <p v-if="feedback" class="inline-feedback" role="status">{{ feedback }}</p>
    <div class="run-summary">
      <div><span>{{ isPreview ? '调用预览' : '决策记录' }}</span><strong>{{ records.length }}</strong></div>
      <div><span>结果证据记录</span><strong>{{ records.filter(item => item.evidence).length }}</strong></div>
      <div><span>父子委派</span><strong class="summary-copy">{{ isPreview ? '未采集' : `${data.child_run_ids.length} 个子运行` }}</strong></div>
      <div><span>哈希链校验</span><strong class="summary-copy">{{ ({ verified: '服务端已验证', invalid: '校验失败', unverified: '不可验证' })[data.chain.status] }}</strong></div>
    </div>
    <p v-if="replayBusy" class="muted" role="status">回放结果返回前，决策筛选、追溯定位和详情视图暂不可切换，以保留当前回放响应。</p>
    <fieldset class="decision-interaction-guard" :disabled="replayBusy" aria-label="决策与追溯">
    <DecisionClasses v-model="decisionClass" :records="records" :preview="isPreview" />
    <DecisionRisks v-if="!isPreview" :records="records" @locate="locateRisk" />
    <div v-if="state === 'ready'" class="run-detail-grid">
      <section aria-label="决策序列" class="decision-sequence">
        <h2>决策序列</h2>
        <div class="history-filters">
          <label>搜索决策<input v-model="query" type="search" placeholder="工具、编号、分类" /></label>
          <label><span id="decision-gate-label">Gate 判定</span><select v-model="gate" aria-labelledby="decision-gate-label"><option>全部判定</option><option>ALLOW</option><option>ASK</option><option>DENY</option><option>REWRITE</option><option>NONE</option></select></label>
          <label><span id="decision-risk-label">异常动作</span><select id="decision-risk-filter" v-model="riskStatus" aria-labelledby="decision-risk-label"><option value="">全部状态</option><option value="UNHOOKED">未覆盖动作</option><option value="UNKNOWN_OUTCOME">执行结果未知</option></select></label>
        </div>
        <p class="muted">{{ filtered.length }} / {{ records.length }} 条{{ isPreview ? '预览' : '决策' }}</p>
        <div v-if="filtered.length" class="decision-list-scroll" tabindex="0" role="region" aria-label="决策列表，可滚动查看全部记录">
        <button v-for="(item, index) in filtered" :key="item.decision_id" class="history-card decision-row" :aria-pressed="current?.decision_id === item.decision_id" @click="selectDecision(item.decision_id)">
          <span class="eyebrow">{{ String(index + 1).padStart(2, '0') }} / {{ item.class }}</span>
          <strong>{{ item.tool_name }}</strong>
          <span class="history-number">{{ item.decision_id }}</span>
          <span class="decision-tag" :data-gate="item.gate">{{ item.gate }}{{ isPreview ? ' · 预览' : '' }}</span>
        </button>
        </div>
        <div v-if="!filtered.length" class="workspace-empty" role="status"><h3>没有匹配的决策</h3><p class="muted">更改搜索词、类别、异常动作或 Gate 筛选。</p><button class="secondary" @click="query = ''; gate = '全部判定'; decisionClass = ''; riskStatus = ''">清除筛选</button></div>
      </section>
      <section class="audit-detail" aria-label="决策详情">
        <template v-if="current">
          <div class="section-heading"><h2 id="selected-decision-heading" tabindex="-1">{{ current.tool_name }}</h2><span class="outline-label">{{ current.status }}</span></div>
          <div class="inspector-tabs audit-tabs" role="toolbar" aria-label="详情视图">
            <button v-for="item in detailViews" :key="item" :aria-pressed="tab === item" :tabindex="tab === item ? 0 : -1" aria-controls="decision-view-content" @keydown="navigateView($event, detailViews, item, selectView)" @click="selectView(item)">{{ item }}</button>
          </div>
          <div id="decision-view-content" role="region" :aria-label="tab">
          <template v-if="tab === '判定与参数'">
            <dl class="audit-facts">
              <dt>决策标识</dt><dd>{{ current.decision_id }}</dd>
              <dt>Gate</dt><dd>{{ current.gate }}{{ isPreview ? ' · 预览' : '' }}</dd>
              <dt>执行策略 Exec</dt><dd>{{ current.exec_policy }}{{ isPreview ? ' · 尚未执行' : '' }}</dd>
              <dt>判定理由</dt><dd>{{ current.gate_reason }}</dd>
              <dt>规则判定耗时</dt><dd>{{ current.gate_latency_ms === null ? '未测量' : `${current.gate_latency_ms} ms` }}</dd>
              <dt>策略 / 白名单</dt><dd>{{ current.policy_version }} / {{ current.whitelist_version }}</dd>
            </dl>
            <h3>脱敏参数摘要预览</h3><pre class="approval-params">{{ current.args_digest }}</pre>
            <h3>改写后参数</h3><p class="muted">{{ current.args_after ?? '无改写预览；真实 REWRITE 需记录重判与 args_after。' }}</p>
          </template>
          <template v-else-if="tab === '事件时间线'">
            
            <EventTimeline v-if="!isPreview" :run-id="run.id" :decision-id="current.decision_id" />
          </template>
          <template v-else-if="tab === '证据与账本'">
            <div v-if="!current.evidence" class="evidence-state"><span class="state-dot" /><div><h3>尚无可核验证据</h3></div></div>
            <dl v-if="current.evidence" class="audit-facts">
              <dt>证据标识</dt><dd>{{ current.evidence.evidence_id }}</dd>
              <dt>证据引用</dt><dd>{{ current.evidence_ref }}</dd>
              <dt>规则快照</dt><dd>{{ current.rule_snapshot_ref ?? '未采集' }}</dd>
              <dt>输入摘要</dt><dd>{{ current.evidence.input_digest || '空证据' }}</dd>
              <dt>返回摘要</dt><dd>{{ current.evidence.output_digest || '空证据' }}</dd>
              <dt>模型 / 参数</dt><dd>{{ current.evidence.model_id }} / {{ current.evidence.model_params }}</dd>
              <dt>判定结果</dt><dd>{{ current.evidence.verdict }}</dd>
              <dt>采集时间</dt><dd>{{ current.evidence.ts ?? '未采集' }}</dd>
              <dt>内容哈希</dt><dd>{{ current.evidence.content_hash ?? '未采集' }}</dd>
            </dl>
            <dl v-else class="audit-facts"><dt>意图记录</dt><dd>{{ isPreview ? '预览未写入' : '此响应未提供，待核对' }}</dd><dt>结果记录</dt><dd>{{ isPreview ? '预览未执行' : '此响应未提供，待核对' }}</dd><dt>证据引用</dt><dd>{{ current.evidence_ref ?? (isPreview ? '未采集' : '未返回引用') }}</dd><dt>规则快照</dt><dd>{{ current.rule_snapshot_ref ?? (isPreview ? '未采集' : '未返回引用') }}</dd><dt>输入 / 输出摘要</dt><dd>未提供正文</dd><dt>模型 / 参数</dt><dd>未提供正文</dd><dt>采集时间</dt><dd>未提供正文</dd><dt>内容哈希 / 前序哈希</dt><dd>未提供核验材料</dd><dt>签名检查点</dt><dd>未提供核验材料</dd></dl>
            <dl v-if="!isPreview" class="audit-facts"><dt>运行哈希链状态</dt><dd>{{ ({ verified: '服务报告已验证', invalid: '服务报告校验失败', unverified: '服务报告不可验证' })[data.chain.status] }}</dd><dt>链状态说明</dt><dd>{{ data.chain.reason || '服务未提供说明' }}</dd></dl>
            
          </template>
          <template v-else>
            <ReplayPanel v-if="!isPreview" :run-id="run.id" :records="records" @busy="replayBusy = $event" @refresh="refreshAudit" />
            <h3>回放前提检查</h3>
            <p class="muted">回放重判由服务端执行。先检查证据和规则快照是否足够，不调用工具、不生成虚假一致率。</p>
            <button class="secondary" @click="replayed = true">检查回放条件</button>
            <div v-if="replayed" class="replay-result" role="status">
              <h3>{{ coverage.evaluable ? '前提检查完成，尚未执行服务端回放' : '当前数据不可回放' }}</h3>
              <p>可评估覆盖：{{ coverage.evaluable }} / {{ coverage.total }}；缺少前提：{{ coverage.missing.length }} 条。</p>
              <p>一致率：未执行回放。缺证据项保留在总决策分母内。</p>
              <ul><li v-for="id in coverage.missing" :key="id">{{ id }} · 缺少结果证据、内容指纹或规则快照</li></ul>
            </div>
          </template>
          </div>
        </template>
        <div v-else class="workspace-empty" role="status"><template v-if="missingSelected"><h3>未找到链接中的决策</h3><p>当前快照不包含 {{ selected }}，请刷新核对或选择其他决策。未自动跳转到首条记录。</p><button class="secondary" @click="selected = ''; persistDecisionLocation()">清除决策定位</button></template><template v-else-if="hiddenSelected"><h3>定位的决策被筛选隐藏</h3><p>{{ selected }} 仍保留定位，未自动改选其他记录。</p><button class="secondary" @click="query = ''; gate = '全部判定'; decisionClass = ''; riskStatus = ''">清除筛选并显示定位决策</button></template><template v-else>选择一条决策以查看详情。</template></div>
      </section>
    </div>
    <RunRelations v-if="!isPreview" :snapshot="data" @open="id => $emit('related', relatedRun(id, run.framework))" />
    <RunClaims v-if="!isPreview" :run-id="run.id" :decision-ids="records.map(item => item.decision_id)" @locate="locateClaimDecision" />
    </fieldset>
    </template>
  </main>
</template>
