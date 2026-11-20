<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { readSettingsLocation, settingsSections, writeSettingsLocation } from './settingsLocation'
import { roles, useSettings } from './settings'
import SessionPanel from './SessionPanel.vue'
import PolicyVersions from './PolicyVersions.vue'
const { mode, data, loading, error, load } = useSettings()
const setting = ref(readSettingsLocation(location.hash)?.category ?? settingsSections[0])
const policyBusy = ref(false), feedback = ref('')
const sessionBusy = ref(false)
const emit = defineEmits<{ busy: [value: boolean] }>()
const writeBusy = computed(() => policyBusy.value || sessionBusy.value)
function saveLocation() {
  if (new URLSearchParams(location.hash.slice(1)).get('section') !== '设置') return
  history.replaceState(null, '', `${location.pathname}${location.search}${writeSettingsLocation(setting.value, mode.value)}`)
}
function restoreLocation() {
  if (writeBusy.value) return
  const value = readSettingsLocation(location.hash)
  if (!value) return
  setting.value = value.category
  mode.value = 'api'
}
watch([setting, mode], saveLocation, { flush: 'post' })
onMounted(() => { saveLocation(); window.addEventListener('popstate', restoreLocation); window.addEventListener('workspace-location', restoreLocation) })
onUnmounted(() => { window.removeEventListener('popstate', restoreLocation); window.removeEventListener('workspace-location', restoreLocation) })
watch(writeBusy, value => emit('busy', value), { flush: 'sync' })
function setPolicyBusy(value: boolean) { policyBusy.value = value }
watch(mode, () => { feedback.value = '' })
const roleRows = [
  { name: '查看账本', access: ['全部', '全部', '本人运行', '全部只读', '所属 Skill'] },
  { name: '配置策略表', access: ['可', '不可', '不可', '不可', '不可'] },
  { name: '切换演示开关', access: ['可', '限单场景', '不可', '不可', '不可'] },
  { name: '导出记录', access: ['可', '可', '本人运行', '可', '可'] },
  { name: '开启发布闸', access: ['可', '不可', '不可', '不可', '可'] },
]
</script>
<template>
  <div class="settings-panel">
    <div class="run-toolbar"><button class="secondary" :disabled="loading || writeBusy" @click="feedback = ''; load()">刷新配置</button></div>
    
    <SessionPanel v-if="mode === 'api'" :locked="policyBusy" @busy="sessionBusy = $event" />
    <p v-if="feedback" class="inline-feedback" role="status">{{ feedback }}</p>
    <div v-if="loading" class="resource-state" role="status"><span class="loading-indicator" /><h2>正在读取身份与配置</h2></div>
    <div v-else-if="error" class="resource-state resource-error" role="alert"><h2>无法读取配置</h2><p>{{ error }}</p><button class="secondary" @click="load">重新读取配置</button></div>
    <template v-else-if="data">
      <p class="muted">快照 {{ data.revision }} · {{ data.source === 'ledger' ? '服务端配置' : '界面预览' }}</p>
      <div class="approval-filters" aria-label="配置分类"><button v-for="name in settingsSections" :key="name" :aria-pressed="setting === name" :disabled="writeBusy" @click="setting = name">{{ name }}</button></div>
      <section class="detail-panel" aria-live="polite">
        <h2>{{ setting }}</h2>
        <template v-if="setting === '身份与权限'">
          <dl v-if="data.identity" class="approval-facts"><dt>当前账号</dt><dd>{{ data.identity.display_name }} · {{ data.identity.user_id }}</dd><dt>角色</dt><dd>{{ data.identity.role }}</dd><dt>数据范围</dt><dd>{{ data.identity.scope }}</dd></dl>
          <p v-else class="inline-feedback">尚无已认证身份。此页不会通过选择角色获得权限。</p>
          <details class="reading-explanation"><summary>查看 PRD v2.7 角色权限基准</summary><div class="evaluation-table-scroll" tabindex="0" role="region" aria-label="角色权限基准，可横向滚动"><table class="evaluation-table role-matrix"><caption>设计基准，具体请求仍需服务端授权</caption><thead><tr><th scope="col">能力</th><th v-for="role in roles" :key="role" scope="col">{{ role }}</th></tr></thead><tbody><tr v-for="row in roleRows" :key="row.name"><th scope="row">{{ row.name }}</th><td v-for="(access, index) in row.access" :key="index" :data-label="roles[index]">{{ access }}</td></tr></tbody></table></div></details>
        </template>
        <template v-else-if="setting === '策略表'">
          <template v-if="data.policy"><dl class="approval-facts"><dt>生效版本</dt><dd>{{ data.policy.version }}</dd><dt>内容指纹</dt><dd>{{ data.policy.content_hash }}</dd></dl><div v-for="rule in data.policy.rules" :key="rule.decision_class" class="settings-row"><strong>{{ rule.decision_class }}</strong><span>{{ rule.gate }} · Exec {{ rule.exec_policy }} · {{ rule.risk }}</span><span v-if="rule.gate === 'ASK'">等待 {{ rule.ask_timeout_seconds }} 秒 · 超时 {{ rule.ask_timeout_policy }}</span></div><p v-if="!data.policy.rules.length" class="inline-feedback">此版本没有规则；缺失决策类规则应 DENY，不能默认放行。</p><details class="reading-explanation"><summary>查看版本变更记录</summary><p v-if="!data.policy.history.length">暂无返回的变更记录。</p><div v-for="(item, index) in data.policy.history" :key="index" class="settings-row"><strong>{{ item.version }}</strong><span>{{ item.actor_id }} · {{ item.at }}</span><span>{{ item.reason }}</span></div></details></template>
          <p v-else class="inline-feedback">尚无生效策略快照。不会用示例规则替代实际策略。</p><p class="muted">策略只读、版本化并保留变更记录。Gate 与 Exec 分开；REWRITE 后必须重新过闸。</p>
          <PolicyVersions v-if="mode === 'api'" :policy="data.policy" :role="data.identity?.role ?? null" :locked="sessionBusy" @busy="setPolicyBusy" @refresh="feedback = ''; load()" @changed="message => { feedback = message; load() }" />
        </template>
        <template v-else-if="setting === '白名单与工具'">
          <template v-if="data.whitelist"><p>白名单 {{ data.whitelist.version }} · 覆盖 {{ data.whitelist.classes.length }} / 11 类</p><ul><li v-for="name in data.whitelist.classes" :key="name">{{ name }}</li></ul><div v-for="tool in data.whitelist.tools" :key="tool.name" class="settings-row"><strong>{{ tool.name }}</strong><span>{{ tool.capability }}</span><span>工作目录 {{ tool.workspace }}</span></div><p v-if="!data.whitelist.tools.length">暂无已声明工具。</p></template><p v-else class="inline-feedback">尚无白名单与工具声明快照。</p><p class="muted">工具能力区分 read_only / mixed / write；未分类副作用必须显式记录 UNHOOKED。</p>
        </template>
        <template v-else><div v-for="framework in data.frameworks" :key="framework.name" class="settings-row"><strong>{{ framework.name }}</strong><span>{{ framework.status === 'verified' ? '已验证' : framework.status === 'unavailable' ? '不可用' : '未验证' }} · {{ framework.version ?? '版本未知' }}</span><span>{{ framework.reason }}</span><span v-if="framework.checked_at">核验时间 {{ framework.checked_at }}</span></div><p class="muted">openJiuwen 锁定 0.1.20.post1；框架升级需重新评测。接入状态不由任务页的框架选择器决定。</p></template>
      </section>
    </template>
  </div>
</template>
