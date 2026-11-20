<script setup lang="ts">
import { onMounted, ref, watch, onScopeDispose } from 'vue'
import { useSession } from './session'
const props = defineProps<{ locked?: boolean }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
function reloadAfterSessionChange() { emit('busy', false); window.location.reload() }
const { data, loading, submitting, stale, error, load, submit } = useSession(reloadAfterSessionChange)
watch(submitting, value => emit('busy', value), { flush: 'sync' })
onScopeDispose(() => emit('busy', false))
const username = ref(''), password = ref(''), logoutConfirmed = ref(false)
onMounted(load)
async function login() {
  if (props.locked) return
  const secret = password.value
  password.value = ''
  await submit('login', username.value, secret)
}
async function logout() {
  if (!logoutConfirmed.value || props.locked) return
  logoutConfirmed.value = false
  await submit('logout')
}
</script>
<template>
  <section class="detail-panel session-panel" aria-label="本机会话">
    <div class="section-heading"><h2>本机会话</h2><button class="secondary" :disabled="loading || submitting || locked" @click="load">重新读取会话</button></div>
    <p class="muted">登录或退出成功后重新加载工作区，清除旧账号在当前页面中的数据。密码不写入本地存储。</p>
    <p v-if="error" role="alert" class="approval-error approval-feedback">{{ error }}</p>
    <p v-if="loading" role="status">正在确认会话…</p>
    <form v-else-if="data && !data.authenticated" class="management-form" @submit.prevent="login">
      <label>账号<input v-model="username" name="username" autocomplete="username" required maxlength="128" :disabled="submitting || stale || locked" /></label>
      <label class="session-password">密码<input v-model="password" type="password" name="password" autocomplete="current-password" required maxlength="1024" :disabled="submitting || stale || locked" /></label>
      <button class="new-task" :disabled="submitting || stale || locked">{{ submitting ? '正在登录…' : '登录本机账号' }}</button>
    </form>
    <template v-else-if="data?.authenticated && data.identity">
      <dl class="approval-facts"><dt>已认证账号</dt><dd>{{ data.identity.display_name }} · {{ data.identity.user_id }}</dd><dt>当前角色</dt><dd>{{ data.identity.role }}</dd><dt>可见范围</dt><dd>{{ data.identity.scope }}</dd></dl>
      <label class="management-confirm"><input v-model="logoutConfirmed" type="checkbox" :disabled="submitting || locked" />确认退出并清除当前页面的账号数据</label><button class="secondary" :disabled="!logoutConfirmed || submitting || locked" @click="logout">{{ submitting ? '正在退出…' : '退出本机账号' }}</button>
    </template>
  </section>
</template>
