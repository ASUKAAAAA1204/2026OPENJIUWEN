<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { parseClaims, type ClaimRecord } from './claims'
const props = defineProps<{ runId: string; decisionIds: string[] }>()
defineEmits<{ locate: [decisionId: string] }>()
const claims = ref<ClaimRecord[]>([]), loading = ref(false), loaded = ref(false), error = ref('')
let active: AbortController | undefined
async function load() {
  active?.abort()
  const controller = new AbortController(); active = controller
  loading.value = true; loaded.value = false; claims.value = []; error.value = ''
  const timer = setTimeout(() => controller.abort(), 12000)
  try {
    const response = await fetch(`/api/v27/runs/${encodeURIComponent(props.runId)}/claims`, { signal: controller.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
    if (!response.ok) throw new Error(({ 401: '会话失效，请重新登录。', 403: '无权读取此运行的断言。', 404: '断言记录或接口尚未提供。' } as Record<number, string>)[response.status] ?? '暂时无法读取断言。')
    const result = parseClaims(await response.json(), props.runId)
    if (active === controller) { claims.value = result; loaded.value = true }
  } catch (cause) { if (active === controller) error.value = controller.signal.aborted ? '读取断言超时，请重试。' : cause instanceof Error ? cause.message : '读取失败。' }
  finally { clearTimeout(timer); if (active === controller) loading.value = false }
}
function reset() { const previous = active; active = undefined; previous?.abort(); claims.value = []; loaded.value = false; loading.value = false; error.value = '' }
watch(() => props.runId, reset)
onScopeDispose(reset)
</script>
<template>
  <section class="detail-panel run-claims" aria-label="终稿断言追溯">
    <div class="section-heading"><h2>终稿断言追溯</h2><button class="secondary" :disabled="loading" @click="load">{{ loading ? '正在读取断言…' : loaded ? '刷新断言' : '读取断言' }}</button></div>
    <p class="muted">断言与决策分开存储，一次决策可产出多条断言。来源与定位来自服务记录，页面不会自行核实外部材料或生成风险标记。</p>
    <p v-if="loading" role="status">正在读取当前运行的断言及关联…</p>
    <p v-else-if="error" role="alert" class="approval-error">{{ error }}</p>
    
    <p v-else-if="!loaded" class="muted">尚未读取断言</p>
    <template v-else>
      <p class="muted">{{ claims.length }} 条断言 · 缺决策 {{ claims.filter(item => !item.decision_id).length }} 条 · 不以断言数量推算声明一致率。</p>
      <p v-if="!claims.length" class="muted">此运行暂无断言记录。空集合不表示声明一致率100%。</p>
      <ul v-else class="claim-records"><li v-for="claim in claims" :key="claim.claim_id">
        <h3>{{ claim.claim_id }}</h3><p>{{ claim.text }}</p>
        <button v-if="claim.decision_id && decisionIds.includes(claim.decision_id)" class="secondary" :aria-label="`定位 ${claim.claim_id} 的关联决策`" @click="$emit('locate', claim.decision_id)">查看关联决策</button>
        <p v-else-if="claim.decision_id" class="inspector-note">当前账本快照未包含此关联决策，暂无法定位。请刷新运行记录核对；引用存在不表示关联已核验。</p>
        <p v-if="!claim.decision_id" class="inspector-note">MISSING_DECISION · 风险未知。NO_EVIDENCE / high，已由服务记录复核引用 {{ claim.missing_decision_mark!.review_ref }}。保留在声明一致率分母及缺证据统计中。</p>
        <dl class="audit-facts"><dt>来源引用</dt><dd>{{ claim.source_ref }}</dd><dt>定位</dt><dd>{{ ({ LOCATED: '已定位', UNLOCATED: '未定位', UNVERIFIABLE: '无法验证' })[claim.location_status] }} · {{ claim.locator ?? '无可核验定位' }}</dd><dt>关联决策</dt><dd>{{ claim.decision_id ?? 'MISSING_DECISION' }}</dd><dt>结果证据</dt><dd>{{ claim.evidence_ref ?? '缺少关联证据' }}</dd><dt>服务验证结论</dt><dd>{{ ({ MATCH: '服务报告一致', MISMATCH: '服务报告不一致', UNKNOWN: '未知，待核查' })[claim.verification] }}</dd></dl>
      </li></ul>
    </template>
  </section>
</template>
