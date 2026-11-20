<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { createPreview, type Framework } from './model'
import GovernanceWorkspace from './GovernanceWorkspace.vue'
import ApprovalWorkspace from './ApprovalWorkspace.vue'
import DebtWorkspace from './DebtWorkspace.vue'
import RunHistory from './RunHistory.vue'
import RunDetail from './RunDetail.vue'
import type { RunPreview } from './model'
import TaskWorkspace from './TaskWorkspace.vue'
import DecisionInspector from './DecisionInspector.vue'
import SessionIndicator from './SessionIndicator.vue'
import '../../styles/workbench.css'
import { readRunLocation, readWorkspaceSection, workspaceSections, writeRunLocation, writeWorkspaceLocation, type WorkspaceSection } from './runLocation'
const initialSection = readWorkspaceSection(location.hash)
const section = ref<WorkspaceSection | '运行详情'>(initialSection ?? '工作台')
let restoringLocation = false
const sectionLocations = new Map<string, string>()
const runOperationBusy = ref(false)
const approvalBusy = ref(false), debtBusy = ref(false)
const governanceBusy = ref(false)
const pendingApprovalId = ref('')
const approvalExecutionRun = ref('')
const operationBusy = computed(() => runOperationBusy.value || approvalBusy.value || debtBusy.value || governanceBusy.value)
let busyLocation = ''
watch(operationBusy, value => { if (value) busyLocation = location.hash }, { flush: 'sync' })
function canNavigate() {
  if (!operationBusy.value) return true
  const operation = runOperationBusy.value ? '运行操作' : approvalBusy.value ? '审批操作' : debtBusy.value ? '债务操作' : '管理操作'
  notice.value = `${operation}正在等待回执，请等待完成或结果未知提示后再离开。关闭页面不能撤销服务端操作。`
  return false
}
function navigate(value: WorkspaceSection) {
  if (operationBusy.value && value === '人工审批' && pendingApprovalId.value) { section.value = value; return }
  if (runOperationBusy.value && section.value === '人工审批' && value === '运行记录' && !approvalBusy.value) { section.value = '运行详情'; return }
  if (canNavigate()) section.value = value
}
function openApproval(id: string | null) {
  if (!id) {
    pendingApprovalId.value = ''
    return
  }
  pendingApprovalId.value = id
  approvalExecutionRun.value = run.value.id
  navigate('人工审批')
}
function guardLink(event: Event) { if (!canNavigate()) event.preventDefault() }
function guardUnload(event: BeforeUnloadEvent) {
  if (!operationBusy.value) return
  event.preventDefault(); event.returnValue = ''
}
const task = ref('')
const tool = ref('write')
const framework = ref<Framework>('openJiuwen')
const executionMode = ref<'preview' | 'api'>('api')
const taskGeneration = ref(0)
const historySource = ref<'preview' | 'api' | undefined>(), historyQuery = ref('')
const historyLookupRevision = ref(0)
function lookupTask(taskId: string) { if (!canNavigate()) return; historyLookupRevision.value++; historyQuery.value = taskId; historySource.value = 'api'; section.value = '运行记录' }
const run = ref(createPreview('RUN-DEMO-001', '检查项目，整理文档修改建议', framework.value))
const history = ref<RunPreview[]>([run.value])
function openRun(value: RunPreview) {
  if (!canNavigate()) return
  if (value.id !== approvalExecutionRun.value) { pendingApprovalId.value = ''; approvalExecutionRun.value = '' }
  run.value = value
  section.value = '运行详情'
  if (value.source === 'ledger') historyPush(writeRunLocation(value))
}
function historyPush(hash: string) {
  const previous = readWorkspaceSection(location.hash)
  if (previous) sectionLocations.set(previous, location.hash)
  if (location.hash !== hash) window.history.pushState(null, '', `${location.pathname}${location.search}${hash}`)
}
function saveWorkspaceSource() {
  if (section.value !== '工作台' || operationBusy.value) return
  const params = new URLSearchParams({ section: '工作台', workspace_source: executionMode.value })
  window.history.replaceState(null, '', `${location.pathname}${location.search}#${params}`)
}
function restoreRunLocation() {
  if (operationBusy.value) {
    const hash = busyLocation || (section.value === '运行详情' ? writeRunLocation(run.value) : writeWorkspaceLocation(section.value))
    window.history.replaceState(null, '', `${location.pathname}${location.search}${hash}`)
    canNavigate()
    return
  }
  restoringLocation = true
  const value = readRunLocation(location.hash)
  if (value) { run.value = value; section.value = '运行详情' }
  else {
    section.value = readWorkspaceSection(location.hash) ?? '工作台'
    if (section.value === '工作台') {
      executionMode.value = 'api'
      saveWorkspaceSource()
    }
  }
  document.title = `${section.value} · TemisGuard`
  restoringLocation = false
}
function composeRun(value: RunPreview) {
  executionMode.value = 'preview'
  run.value = value
  task.value = value.title
  framework.value = value.framework
  tool.value = value.tools[0]?.id ?? ''
  tab.value = '决策'
  section.value = '工作台'
  notice.value = '正在查看历史演示任务，未执行真实工具。'
}
const selectedTool = computed(() => run.value.tools.find((item) => item.id === tool.value) ?? null)
let sequence = 1
const tab = ref('决策')
const notice = ref('')
function revealNavigation() {
  const active = document.querySelector<HTMLElement>('.workbench-nav nav [aria-current="page"]')
  const nav = active?.parentElement
  if (!active || !nav || nav.scrollWidth <= nav.clientWidth) return
  const item = active.getBoundingClientRect(), viewport = nav.getBoundingClientRect()
  if (item.right > viewport.right) nav.scrollLeft += item.right - viewport.right
  else if (item.left < viewport.left) nav.scrollLeft -= viewport.left - item.left
}
onMounted(() => { window.addEventListener('resize', revealNavigation); window.addEventListener('popstate', restoreRunLocation); window.addEventListener('beforeunload', guardUnload); restoreRunLocation() })
onUnmounted(() => { window.removeEventListener('resize', revealNavigation); window.removeEventListener('popstate', restoreRunLocation); window.removeEventListener('beforeunload', guardUnload) })
function restoreHashLocation() { restoreRunLocation(); window.dispatchEvent(new Event('workspace-location')) }
onMounted(() => window.addEventListener('hashchange', restoreHashLocation))
onUnmounted(() => window.removeEventListener('hashchange', restoreHashLocation))
watch(section, async (value) => {
  if (!restoringLocation) {
    if (value === '运行详情' && run.value.source === 'ledger') historyPush(writeRunLocation(run.value))
    else if (value !== '运行详情') {
      historyPush(sectionLocations.get(value) ?? writeWorkspaceLocation(value))
      window.dispatchEvent(new Event('workspace-location'))
    }
  }
  notice.value = ''
  document.title = `${value} · TemisGuard`
  await nextTick()
  revealNavigation()
}, { flush: 'sync' })
watch(executionMode, saveWorkspaceSource, { flush: 'post' })
function reset() {
  if (!canNavigate()) return
  pendingApprovalId.value = ''; approvalExecutionRun.value = ''
  taskGeneration.value++
  section.value = '工作台'
  task.value = ''
  notice.value = ''
  framework.value = 'openJiuwen'
  tool.value = ''
  tab.value = '决策'
  run.value = createPreview('', '', framework.value)
  requestAnimationFrame(() => document.getElementById('task-input')?.focus())
}
function submit() {
  if (!task.value.trim()) return
  run.value = createPreview(
    `RUN-DEMO-${String(++sequence).padStart(3, '0')}`,
    task.value.trim(),
    framework.value,
  )
  history.value = [run.value, ...history.value]
  tool.value = 'write'
  tab.value = '决策'
  notice.value = '已创建本地演示任务。未调用模型或执行工具。'
}
</script>
<template>
  <div class="workbench-shell">
    <a class="skip-link" href="#workspace-content">跳转到工作区</a>
    <aside class="workbench-nav">
      <a class="brand" href="?" @click="guardLink"
        >T<span>TEMISGUARD<small>天序明衡</small></span></a
      ><button class="new-task" @click="reset">＋ 新建任务</button>
      <div class="eyebrow nav-caption">WORKSPACE</div>
      <nav aria-label="主导航">
        <button
          v-for="(item, index) in workspaceSections.slice(0, 5)"
          :key="item"
          :aria-current="section === item || (item === '运行记录' && section === '运行详情') ? 'page' : undefined"
          @click="navigate(item)"
        >
          <span class="nav-index">0{{ index + 1 }}</span
          >{{ item }}
        </button>
      </nav>
      <button
        class="settings-link"
        :aria-current="section === '设置' ? 'page' : undefined"
        @click="navigate('设置')"
      >
        ⚙ 设置
      </button>
      <div class="nav-footer">
        <span class="local-dot" />本机工作空间<small>v2.7</small
        ><a href="?view=legacy" @click="guardLink">旧版 Studio ↗</a>
      </div>
    </aside>
    <div class="workbench-body">
      <header class="workbench-topbar">
        <span>工作空间 <span class="separator">/</span> {{ section }}</span>
        <SessionIndicator @settings="navigate('设置')" />
      </header>
      <Transition name="notice">
        <div v-if="notice" class="demo-notice" role="status">
          <span>{{ notice }}</span>
          <button aria-label="关闭提示" @click="notice = ''">×</button>
        </div>
      </Transition>
      <div
        id="workspace-content"
        tabindex="-1"
        class="workbench-content"
        :class="{ 'wide-content': section !== '工作台' || executionMode === 'api' }"
      >
        <GovernanceWorkspace
          v-if="['版本评估', '设置'].includes(section)"
          :section="section"
          :runs="history"
          @inspect="openRun"
          @lookup="lookupTask"
          @busy="governanceBusy = $event"
        />
        <ApprovalWorkspace v-if="section === '人工审批'" :focus-id="pendingApprovalId" :execution-run-id="approvalExecutionRun || undefined" :execution-waiting="!!pendingApprovalId" @busy="approvalBusy = $event" @return-execution="section = '运行详情'" />
        <DebtWorkspace v-if="section === '证据与债务'" @busy="debtBusy = $event" />
        <KeepAlive>
        <RunHistory
          v-if="section === '运行记录'"
          :runs="history"
          :active-id="run.id"
          :initial-source="historySource"
          :initial-query="historyQuery"
          :lookup-revision="historyLookupRevision"
          @open="openRun"
        />
        </KeepAlive>
        <KeepAlive>
          <RunDetail v-if="section === '运行详情'" :run="run" @busy="runOperationBusy = $event" @approval="openApproval" @back="navigate('运行记录')" @compose="composeRun" @related="openRun" />
        </KeepAlive>
        <TaskWorkspace v-if="section === '工作台'"
          :key="taskGeneration"
          v-model:task="task"
          v-model:framework="framework"
          v-model:execution-mode="executionMode"
          :run="run"
          v-model:tool="tool"
          :section="section"
          @submit="submit"
          @busy="runOperationBusy = $event"
          @created="openRun"
          @history="lookupTask"
        /><DecisionInspector
          v-if="section === '工作台' && executionMode === 'preview'"
          :tool="selectedTool"
          :run="run"
          :selected="tab"
          @select="tab = $event"
        />
      </div>
      <footer class="workbench-footer">
        <span>TRACEABLE BY DESIGN</span><span>决策 → 证据 → 复核</span>
      </footer>
    </div>
  </div>
</template>
