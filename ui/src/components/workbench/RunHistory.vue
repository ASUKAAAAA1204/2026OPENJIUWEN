<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onScopeDispose, ref, watch } from 'vue'
import type { RunPreview } from './model'
import ServiceRunHistory from './ServiceRunHistory.vue'
import { readHistoryLocation, replaceHistoryLocation } from './historyLocation'
defineEmits<{ open: [run: RunPreview] }>()
const props = defineProps<{ runs: RunPreview[]; activeId: string; initialSource?: 'preview' | 'api'; initialQuery?: string; lookupRevision?: number }>()
const query = ref('')
const framework = ref('全部框架')
const source = ref(props.initialSource ?? 'api')
let restoringSource = false
watch(source, value => { if (!restoringSource) replaceHistoryLocation({ source: value, framework: '全部框架', state: '全部状态' }) }, { flush: 'sync' })
function restoreSource() {
  const restored = readHistoryLocation(location.hash)
  if (!restored) return
  restoringSource = true; source.value = restored.source; restoringSource = false
}
function listen() { window.addEventListener('popstate', restoreSource); window.addEventListener('workspace-location', restoreSource) }
function unlisten() { window.removeEventListener('popstate', restoreSource); window.removeEventListener('workspace-location', restoreSource) }
onMounted(listen); onActivated(listen); onDeactivated(unlisten); onScopeDispose(unlisten)
onActivated(() => { if (source.value === 'preview') replaceHistoryLocation({ source: 'preview', framework: '全部框架', state: '全部状态' }) })
watch(() => [props.initialSource, props.initialQuery], () => { if (props.initialSource) source.value = props.initialSource })
const filtered = computed(() =>
  props.runs.filter(
    (run) =>
      (framework.value === '全部框架' || run.framework === framework.value) &&
      `${run.id} ${run.title}`.toLowerCase().includes(query.value.trim().toLowerCase()),
  ),
)
</script>
<template>
  <main class="task-workspace run-history">
    <div class="workspace-heading">
      <div>
        <p class="eyebrow">RUN ARCHIVE / 02</p>
        <h1>运行记录</h1>
        <p class="muted">回看任务，检查每一次关键决策。</p>
      </div>
      <span class="outline-label">{{ source === 'api' ? '账本查询' : `${runs.length} 条演示记录` }}</span>
    </div>
    
    <ServiceRunHistory v-if="source === 'api'" :key="lookupRevision" :initial-query="initialQuery" @open="$emit('open', $event)" />
    <template v-else>
    <div class="history-filters">
      <label>搜索任务<input v-model="query" type="search" placeholder="任务名称或运行编号" /></label
      ><label
        >运行框架<select v-model="framework">
          <option>全部框架</option>
          <option>openJiuwen</option>
          <option>LangGraph</option>
          <option>OpenAI Agents SDK</option>
        </select></label
      >
    </div>
    <div class="section-heading">
      <h2>会话历史</h2>
      <span class="eyebrow">{{ filtered.length }} RESULTS</span>
    </div>
    <p class="history-note muted">
      仅保留当前页面会话，刷新后恢复初始示例。以下记录均未执行真实工具。
    </p>
    <ul v-if="filtered.length" class="history-list">
      <li v-for="item in filtered" :key="item.id">
        <button
          class="history-card"
          :aria-current="item.id === activeId ? 'true' : undefined"
          @click="$emit('open', item)"
        >
          <span class="history-number">{{ item.id }}</span
          ><strong>{{ item.title }}</strong
          ><span class="history-meta"
            ><span>{{ item.framework }}</span
            ><span
              >{{ item.tools.filter((tool) => tool.gate !== 'NONE').length }} 项关键决策预览</span
            ><span class="blue-label">查看任务 ↗</span></span
          >
        </button>
      </li>
    </ul>
    <div v-else class="workspace-empty" role="status">
      <h2>没有匹配的运行记录</h2>
      <p class="muted">尝试修改搜索词或清除框架筛选。</p>
      <button
        class="new-task"
        @click="
          query = '';
          framework = '全部框架'
        "
      >
        清除筛选
      </button>
    </div>
    </template>
  </main>
</template>
