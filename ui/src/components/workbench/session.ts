import { onScopeDispose, ref, shallowRef } from 'vue'
import { roles } from './settings'
export interface SessionSnapshot {
  authenticated: boolean; csrf_token: string
  identity: { user_id: string; display_name: string; role: typeof roles[number]; scope: string } | null
}
export function parseSession(value: unknown): SessionSnapshot {
  if (typeof value !== 'object' || value === null) throw new Error('会话响应格式不正确，请重新读取。')
  const data = value as Record<string, unknown>, identity = data.identity as Record<string, unknown> | null
  if (typeof data.authenticated !== 'boolean' || typeof data.csrf_token !== 'string' || !data.csrf_token.trim() ||
    (data.authenticated ? !identity || typeof identity !== 'object' || !['user_id', 'display_name', 'scope'].every(key => typeof identity[key] === 'string' && !!String(identity[key]).trim()) || !roles.includes(identity.role as typeof roles[number]) : identity !== null)) throw new Error('会话身份与认证状态不一致，请重新读取。')
  return data as unknown as SessionSnapshot
}
export function useSession(onChanged: () => void) {
  const data = shallowRef<SessionSnapshot | null>(null), loading = ref(false), submitting = ref(false), stale = ref(false), error = ref('')
  let controller: AbortController | undefined, revision = 0
  async function request(signal: AbortSignal, body?: Record<string, string>) {
    const response = await fetch(body ? `/api/v27/session/${body.action}` : '/api/v27/session', { method: body ? 'POST' : 'GET', signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json', 'X-CSRF-Token': data.value!.csrf_token } : {}) }, ...(body ? { body: JSON.stringify(body.action === 'login' ? { username: body.username, password: body.password } : {}) } : {}) })
    if (!response.ok) throw new Error(({ 401: '账号或密码不正确，或会话已失效。', 403: '会话校验失败，请重新读取后操作。', 404: '本机会话服务尚未提供此接口。', 429: '尝试过于频繁，请稍后重试。' } as Record<number, string>)[response.status] ?? '会话服务暂时不可用，请先重新读取状态。')
    return parseSession(await response.json())
  }
  async function load() {
    if (submitting.value) return
    const token = ++revision
    controller?.abort(); controller = new AbortController()
    const active = controller, timer = setTimeout(() => active.abort(), 12000)
    data.value = null; error.value = ''; loading.value = true; stale.value = false
    try { const result = await request(active.signal); if (token === revision) data.value = result }
    catch (cause) { if (token === revision) error.value = cause instanceof Error && !active.signal.aborted ? cause.message : '读取会话超时，请重试。' }
    finally { clearTimeout(timer); if (token === revision) loading.value = false }
  }
  async function submit(action: 'login' | 'logout', username = '', password = '') {
    if (submitting.value || loading.value || stale.value || !data.value) return false
    error.value = ''
    if (action === 'login' && (!username.trim() || !password)) { error.value = '请输入账号和密码。'; return false }
    if ((action === 'login') === data.value.authenticated) { error.value = '会话状态已变化，请重新读取。'; return false }
    submitting.value = true
    const token = revision, active = new AbortController(); controller = active
    const timer = setTimeout(() => active.abort(), 12000)
    try {
      const result = await request(active.signal, { action, username: username.trim(), password })
      if (result.authenticated !== (action === 'login')) throw new Error('返回的会话状态与操作不一致，请重新读取。')
      if (token !== revision) return false
      data.value = result
      onChanged()
      return true
    } catch (cause) {
      if (token === revision) { stale.value = true; data.value = null; error.value = `${cause instanceof Error && !active.signal.aborted ? cause.message : '提交结果未知。'} 请重新读取会话后核对，勿重复提交。` }
      return false
    } finally { clearTimeout(timer); if (token === revision) submitting.value = false }
  }
  onScopeDispose(() => { revision++; controller?.abort(); data.value = null })
  return { data, loading, submitting, stale, error, load, submit }
}
