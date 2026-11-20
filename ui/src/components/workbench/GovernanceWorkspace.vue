<script setup lang="ts">
import type { RunPreview } from './model'
import EvaluationPanel from './EvaluationPanel.vue'
import SettingsPanel from './SettingsPanel.vue'
defineProps<{ section: string; runs: RunPreview[] }>()
const emit = defineEmits<{ inspect: [run: RunPreview]; busy: [value: boolean]; lookup: [runId: string] }>()
</script>
<template>
  <main class="task-workspace governance-workspace">
    <div class="workspace-heading">
      <div>
        <p class="eyebrow">
          ASSURANCE /
          {{ section === '设置' ? 'CONFIGURATION' : section === '版本评估' ? '05' : '04' }}
        </p>
        <h1>{{ section }}</h1>
        <p class="muted">
          {{
            section === '证据与债务'
              ? '定位证据缺口，保留每一次消解依据。'
              : section === '版本评估'
                ? '以相同口径，比较可核验的运行读数。'
                : '控制接入范围，让规则与版本保持明确。'
          }}
        </p>
      </div>
      <span v-if="section === '设置'" class="outline-label">配置与权限</span>
    </div>
    <EvaluationPanel v-if="section === '版本评估'" :runs="runs" @busy="emit('busy', $event)" @inspect="emit('inspect', $event)" @lookup="emit('lookup', $event)" />
    <SettingsPanel v-else @busy="emit('busy', $event)" />
  </main>
</template>
