import { parseSession } from './session'

/** Read a fresh cookie-bound token for one write; never retain it in storage. */
export async function writeSessionHeaders(signal: AbortSignal): Promise<Record<string, string>> {
  const response = await fetch('/api/v27/session', { signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error('无法确认登录状态，本次操作尚未提交。请在设置中登录后刷新当前数据。')
  let session
  try { session = parseSession(await response.json()) }
  catch { throw new Error('会话响应无效，本次操作尚未提交。请在设置中重新读取会话。') }
  if (!session.authenticated) throw new Error('登录已失效，本次操作尚未提交。请在设置中登录后刷新当前数据。')
  signal.throwIfAborted()
  return { 'X-CSRF-Token': session.csrf_token }
}
