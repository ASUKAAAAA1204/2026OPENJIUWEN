<script setup lang="ts">
import { ref } from 'vue'
import type { Framework, RunPreview } from './model'
import RunCreatePanel from './RunCreatePanel.vue'
defineProps<{
  section: string
  task: string
  tool: string
  framework: Framework
  run: RunPreview
  executionMode: 'preview' | 'api'
}>()
const emit = defineEmits<{
  'update:task': [value: string]
  'update:tool': [value: string]
  'update:framework': [value: Framework]
  submit: []
  'update:executionMode': [value: 'preview' | 'api']
  created: [run: RunPreview]
  history: [taskId: string]
  busy: [value: boolean]
}>()
const createBusy = ref(false)
function setCreateBusy(value: boolean) { createBusy.value = value; emit('busy', value) }
</script>
<template>
  <main class="task-workspace">
    <div class="workspace-heading">
      <div>
        <p class="eyebrow">AGENT WORKSPACE / 01</p>
        <h1>{{ section }}</h1>
        <p class="muted">让每一次行动，都有据可查。</p>
      </div>
      <span class="outline-label">{{ executionMode === 'api' ? '账本服务' : '本地演示' }}</span>
    </div>
    <template v-if="section === '工作台'">
      
      
      <RunCreatePanel v-if="executionMode === 'api'" @busy="setCreateBusy" @created="emit('created', $event)" @history="emit('history', $event)" />
      <template v-else>
      <section class="task-composer" aria-label="任务输入">
        <div class="composer-top">
          <label for="framework">运行框架</label
          ><select
            id="framework"
            :value="framework"
            @change="
              emit('update:framework', ($event.target as HTMLSelectElement).value as Framework)
            "
          >
            <option>openJiuwen</option>
            <option>LangGraph</option>
            <option>OpenAI Agents SDK</option>
          </select>
        </div>
        <label class="sr-only" for="task-input">任务描述</label
        ><textarea
          id="task-input"
          :value="task"
          placeholder="描述任务，例如：检查项目文档，生成修改建议…"
          @input="emit('update:task', ($event.target as HTMLTextAreaElement).value)"
        />
        <div class="composer-bottom">
          <span>受控工作目录 · 演示环境</span
          ><button class="primary" :disabled="!task.trim()" @click="emit('submit')">
            创建演示任务 ↗
          </button>
        </div>
      </section>
      <section v-if="run.id" class="execution-section">
        <div class="section-heading">
          <h2>执行过程</h2>
          <span class="eyebrow">{{ run.id }}</span>
        </div>
        <div class="timeline-entry">
          <span class="step-index">01</span>
          <div>
            <p class="eyebrow">TASK RECEIVED</p>
            <h3>{{ run.title }}</h3>
            <p class="muted">{{ run.framework }} · 演示预览，未执行</p>
          </div>
        </div>
        <div class="timeline-entry">
          <span class="step-index">02</span>
          <div class="tool-group">
            <p class="eyebrow">TOOL CALLS</p>
            <button
              v-for="item in run.tools"
              :key="item.id"
              class="tool-card"
              :aria-pressed="tool === item.id"
              @click="emit('update:tool', item.id)"
            >
              <span
                ><strong>{{ item.title }}</strong
                ><small>{{
                  `${item.name} · ${item.gate === 'NONE' ? '只读工具' : '关键决策'}`
                }}</small></span
              ><span class="blue-label"
                >{{ item.gate === 'NONE' ? '只读' : 'ALLOW · 演示' }} ↗</span
              >
            </button>
          </div>
        </div>
        <div class="timeline-entry">
          <span class="step-index hollow">03</span>
          <div>
            <h3>等待接入真实运行</h3>
            <p class="muted">选择工具，在右侧检查判定与证据。</p>
          </div>
        </div>
      </section>
      <section v-else class="workspace-empty">
        <h2>开始一个新任务</h2>
        <p class="muted">选择框架并输入任务，创建本地演示预览。</p>
      </section>
      </template>
    </template>
    <section v-else class="workspace-empty">
      <span class="empty-number"
        >0{{ ['运行记录', '人工审批', '不确定债务', '版本与策略'].indexOf(section) + 2 }}</span
      >
      <h2>{{ section }}</h2>
      
      <span class="outline-label">界面结构阶段</span>
    </section>
  </main>
</template>
