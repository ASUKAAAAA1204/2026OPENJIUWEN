<script setup lang="ts">
import type { RunPreview, ToolPreview } from './model'
import { navigateView } from './viewNavigation'
defineProps<{ tool: ToolPreview | null; run: RunPreview; selected: string }>()
const emit = defineEmits<{ select: [value: string] }>()
const views = ['决策', '证据'] as const
</script>
<template>
  <aside class="inspector" aria-label="决策检查器">
    <div class="eyebrow">INSPECTOR / 03</div>
    <h2>决策检查器</h2>
    <div class="inspector-tabs" role="toolbar" aria-label="检查器视图">
      <button
        v-for="item in ['决策', '证据']"
        :key="item"
        :aria-pressed="selected === item"
        :tabindex="selected === item ? 0 : -1"
        aria-controls="inspector-view-content"
        @keydown="navigateView($event, views, item, value => emit('select', value))"
        @click="$emit('select', item)"
      >
        {{ item }}
      </button>
    </div>
    <div id="inspector-view-content" role="region" :aria-label="selected">
    <div v-if="!tool" class="empty-evidence">尚未选择决策，请先创建任务。</div>
    <template v-else-if="selected === '决策'">
      <p class="eyebrow">当前工具</p>
      <h3>{{ tool?.title }}</h3>
      <dl class="facts">
        <dt>Gate 判定</dt>
        <dd>
          <span class="blue-label">{{
            tool?.gate === 'NONE' ? 'NONE · 只读' : 'ALLOW · 演示'
          }}</span>
        </dd>
        <dt>执行方式</dt>
        <dd>normal</dd>
        <dt>所属任务</dt>
        <dd>{{ run.id }}</dd>
        <dt>策略版本</dt>
        <dd>示例策略 v0</dd>
      </dl>
      <h3 class="section-label">参数摘要</h3>
      <pre>{{ JSON.stringify(tool?.args, null, 2) }}</pre>
      <div class="inspector-note">记录应当先于动作。<br />此处为界面示例，尚未执行工具。</div>
    </template>
    <template v-else
      ><h3>证据预览</h3>
      <p class="muted">选择的工具：{{ tool?.title }}</p>
      <div class="empty-evidence">
        {{ tool?.evidence }}<br />尚无真实证据快照<br /><small
          >接入账本后显示摘要、哈希与来源</small
        >
      </div></template
    >
    </div>
  </aside>
</template>
