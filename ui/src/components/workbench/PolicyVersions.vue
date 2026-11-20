<script setup lang="ts">
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import type { PolicySnapshot } from './settings'
import { matchesPolicySettings, parsePolicyCatalog, parsePolicySwitch, policyChanges, policyRequest, type PolicyCatalog } from './policies'
const props = defineProps<{ policy: PolicySnapshot | null; role: string | null; locked?: boolean }>()
const emit = defineEmits<{ changed: [message: string]; refresh: []; busy: [value: boolean] }>()
const data = shallowRef<PolicyCatalog | null>(null), loading = ref(false), busy = ref(false), stale = ref(false), error = ref('')
const selected = ref(''), reason = ref(''), confirmed = ref(false)
const active = computed(() => data.value?.versions.find(item => item.version === data.value?.active?.version) ?? null)
const candidate = computed(() => data.value?.versions.find(item => item.version === selected.value) ?? null)
const changes = computed(() => candidate.value ? policyChanges(active.value, candidate.value) : [])
const canSwitch = computed(() => props.role === '平台管理员' && data.value?.can_switch)
let revision = 0, controller: AbortController | undefined
async function load() {
  if (busy.value || props.locked) return
  const token = ++revision; controller?.abort(); const request = new AbortController(); controller = request
  const timer = setTimeout(() => request.abort(), 12000)
  loading.value = true; data.value = null; selected.value = ''; reason.value = ''; confirmed.value = false; error.value = ''; stale.value = false
  try {
    const catalog = parsePolicyCatalog(await policyRequest(request.signal))
    if (token !== revision) return
    if (!matchesPolicySettings(catalog, props.policy)) { stale.value = true; throw new Error('策略目录与配置快照不一致，请刷新配置重新核对。') }
    if (catalog.can_switch && props.role !== '平台管理员') { stale.value = true; throw new Error('策略切换权限与当前身份不一致，请刷新配置确认权限。') }
    data.value = catalog
  } catch (cause) { if (token === revision) error.value = request.signal.aborted ? '读取策略目录超时，请重试。' : cause instanceof Error ? cause.message : '无法读取策略目录。' }
  finally { clearTimeout(timer); if (token === revision) loading.value = false }
}
async function activate() {
  if (props.locked || loading.value || busy.value || stale.value || !data.value || !candidate.value || !canSwitch.value) return
  error.value = ''
  const previous = data.value, target = candidate.value, note = reason.value.trim()
  if (!target.eligible || target.version === previous.active?.version) { error.value = '请选择一个允许启用且尚未生效的版本。'; return }
  if (!note || !confirmed.value) { error.value = '请填写切换原因，并确认已核对规则差异与影响。'; return }
  const token = revision, request = new AbortController(); controller = request
  const timer = setTimeout(() => request.abort(), 12000), requestId = crypto.randomUUID()
  busy.value = true
  try {
    const receipt = parsePolicySwitch(await policyRequest(request.signal, { request_id: requestId, expected_revision: previous.revision, expected_active_version: previous.active?.version ?? null, expected_active_hash: previous.active?.content_hash ?? null, target_version: target.version, target_hash: target.content_hash, reason: note }), previous, target, requestId, note)
    if (token !== revision) return
    data.value = receipt.catalog
    emit('changed', `服务端已确认切换至 ${target.version}。操作人 ${receipt.audit.actor_id} · ${receipt.audit.at} · 审计 ${receipt.audit.audit_id}。历史决策仍保留原规则快照。`)
  } catch (cause) { if (token === revision) { stale.value = true; error.value = request.signal.aborted ? '切换响应超时，结果未知。请刷新配置确认生效版本，勿重复提交。' : cause instanceof Error ? cause.message : '切换结果未知，请刷新配置核对。' } }
  finally { clearTimeout(timer); if (token === revision) { busy.value = false; confirmed.value = false } }
}
watch(selected, () => { confirmed.value = false; reason.value = ''; if (!stale.value) error.value = '' })
watch(() => props.locked, locked => { if (locked) confirmed.value = false }, { flush: 'sync' })
watch(busy, value => emit('busy', value), { flush: 'sync' })
watch(() => [props.policy, props.role], () => { revision++; controller?.abort(); busy.value = false; void load() }, { immediate: true })
onScopeDispose(() => { revision++; controller?.abort(); emit('busy', false) })
</script>
<template>
  <section class="policy-versions" aria-label="策略版本切换">
    <div class="section-heading"><h3>已发布版本与切换</h3><button class="secondary" :disabled="busy || loading || stale || locked" @click="load">读取版本目录</button></div>
    <p v-if="locked" class="inline-feedback" role="status">会话操作正在等待回执，暂不能切换策略；完成后需重新确认操作范围。</p>
    <p class="muted">配置内容只读。启用或回滚均切换到已发布版本；已有判定、审批与账本记录继续引用原快照。</p>
    <p v-if="loading" role="status">正在读取已发布策略版本…</p>
    <div v-if="error" class="approval-error approval-feedback" role="alert"><p>{{ error }}</p><button class="text-action" :disabled="busy || locked" @click="emit('refresh')">刷新配置并重新核对</button></div>
    <template v-if="data">
      <p class="muted">目录修订 {{ data.revision }} · {{ data.versions.length }} 个可见版本</p>
      <p v-if="!data.versions.length" class="inline-feedback">暂无可见的已发布版本，不能切换策略。</p>
      <template v-else>
        <label class="policy-choice"><span id="policy-candidate-label">候选策略版本</span><select v-model="selected" aria-labelledby="policy-candidate-label" :disabled="busy || stale || locked"><option value="" disabled>选择版本并查看差异</option><option v-for="item in data.versions" :key="item.version" :value="item.version">{{ item.version }}{{ item.version === data.active?.version ? ' · 当前生效' : '' }}{{ item.eligible ? '' : ' · 暂不可启用' }}</option></select></label>
        <template v-if="candidate">
          <dl class="audit-facts"><dt>候选版本</dt><dd>{{ candidate.version }} · {{ candidate.description }}</dd><dt>发布主体</dt><dd>{{ candidate.published_by }} · {{ candidate.published_at }}</dd><dt>内容指纹</dt><dd>{{ candidate.content_hash }}</dd><dt>启用条件</dt><dd>{{ candidate.reason }}</dd></dl>
          
          <p v-if="candidate.version === data.active?.version" class="inline-feedback">所选版本已生效，无需重复切换。</p>
          <template v-else>
            <h4>配置差异 · {{ changes.length }} 项</h4>
            <p class="muted">请逐项核对变更。移除决策类规则后，该类动作默认 DENY。</p>
            <table v-if="changes.length" class="evaluation-table policy-diff"><caption>当前 {{ active?.version ?? '无生效策略' }} → 候选 {{ candidate.version }}</caption><thead><tr><th scope="col">字段</th><th scope="col">当前配置</th><th scope="col">候选配置</th></tr></thead><tbody><tr v-for="item in changes" :key="item.field"><th scope="row">{{ item.field }}</th><td data-label="当前配置"><pre>{{ item.before }}</pre></td><td data-label="候选配置"><pre>{{ item.after }}</pre></td></tr></tbody></table>
            
          </template>
          <details class="reading-explanation"><summary>查看当前与候选的完整只读配置</summary><div v-if="active"><h4>当前 {{ active.version }}</h4><pre class="policy-configuration" tabindex="0" role="region" aria-label="当前完整配置">{{ JSON.stringify(active.configuration, null, 2) }}</pre></div><h4>候选 {{ candidate.version }}</h4><pre class="policy-configuration" tabindex="0" role="region" aria-label="候选完整配置">{{ JSON.stringify(candidate.configuration, null, 2) }}</pre></details>
          <form v-if="canSwitch && candidate.version !== data.active?.version" class="policy-switch-form" @submit.prevent="activate">
            <fieldset :disabled="busy || stale || locked || !candidate.eligible">
              <label>策略切换原因<textarea v-model="reason" rows="3" maxlength="1000" placeholder="说明启用或回滚此版本的依据" /></label>
              <label class="management-confirm"><input v-model="confirmed" type="checkbox" />我已核对完整配置差异、启用范围与历史快照边界</label>
              <button class="primary" :disabled="!confirmed || !reason.trim()" type="submit">{{ busy ? '正在切换策略…' : '确认切换策略版本' }}</button>
            </fieldset>
          </form>
          <p v-if="!canSwitch" class="inline-feedback">当前账号只能查看版本，切换需要平台管理员的服务端授权。</p>
        </template>
      </template>
    </template>
    <p class="muted">离开页面会取消等待，不能撤销服务端已完成的切换。结果未知时先刷新确认，系统不会自动重放请求。</p>
  </section>
</template>
