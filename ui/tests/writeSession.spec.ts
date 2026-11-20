import { afterEach, describe, expect, it, vi } from 'vitest'
import { writeSessionHeaders } from '../src/components/workbench/writeSession'
const session = { authenticated: true, csrf_token: 'token-1', identity: { user_id: 'u', display_name: 'User', role: '开发者', scope: 'test' } }
afterEach(() => vi.unstubAllGlobals())
describe('写入前会话检查', () => {
  it('每次重新读取令牌，不复用旧会话并禁止缓存', async () => {
    let sequence = 0
    const fetcher = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ...session, csrf_token: `token-${++sequence}` }))))
    vi.stubGlobal('fetch', fetcher)
    const signal = new AbortController().signal
    expect(await writeSessionHeaders(signal)).toEqual({ 'X-CSRF-Token': 'token-1' })
    expect(await writeSessionHeaders(signal)).toEqual({ 'X-CSRF-Token': 'token-2' })
    expect(fetcher.mock.calls).toHaveLength(2)
    expect(fetcher).toHaveBeenLastCalledWith('/api/v27/session', expect.objectContaining({ signal, credentials: 'same-origin', cache: 'no-store' }))
  })
  it('匿名、失效、无效响应都不授权写入', async () => {
    for (const response of [new Response('{}', { status: 401 }), new Response(JSON.stringify({ authenticated: false, csrf_token: 'anonymous', identity: null })), new Response(JSON.stringify({ ...session, csrf_token: '' }))]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
      await expect(writeSessionHeaders(new AbortController().signal)).rejects.toThrow('尚未提交')
    }
  })
  it('读取期间取消后不返回提交凭据', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => { controller.abort(); return Promise.resolve(new Response(JSON.stringify(session))) }))
    await expect(writeSessionHeaders(controller.signal)).rejects.toThrow()
  })
})
