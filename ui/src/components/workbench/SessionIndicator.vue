<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useSession } from './session'
defineEmits<{ settings: [] }>()
const { data, loading, error, load } = useSession(() => {})
const label = computed(() => loading.value ? '正在确认身份' : data.value?.authenticated ? `${data.value.identity!.display_name} · ${data.value.identity!.role}` : data.value ? '未登录' : '会话未确认')
const description = computed(() => data.value?.identity ? `账号 ${data.value.identity.user_id}，可见范围 ${data.value.identity.scope}。身份来自最近一次会话读取，每次写入仍重新校验。` : error.value || '请登录账号。')
async function refresh() {
  const previous = data.value?.identity ? JSON.stringify(data.value.identity) : null
  await load()
  // A confirmed account change must clear every mounted workspace's old data.
  if (previous && data.value && previous !== JSON.stringify(data.value.identity)) window.location.reload()
}
onMounted(load)
</script>
<template>
  <div class="session-indicator" aria-label="全局会话状态">
    <button class="session-identity" :title="description" :aria-label="`${label}，打开账号与权限设置`" aria-describedby="global-session-description" @click="$emit('settings')">
      <span class="session-state-dot" :class="{ 'is-authenticated': data?.authenticated }" aria-hidden="true" />
      <span class="session-identity-label" aria-live="polite">{{ label }}</span>
    </button>
    <span id="global-session-description" class="sr-only">{{ description }}</span>
    <button class="session-refresh" :disabled="loading" aria-label="刷新全局会话状态" title="重新确认会话；账号变更时重新加载并清除旧数据" @click="refresh">↻</button>
  </div>
</template>
