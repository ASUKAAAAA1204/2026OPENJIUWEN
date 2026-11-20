import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { parseSession, useSession } from '../src/components/workbench/session'
afterEach(() => vi.unstubAllGlobals())
describe('会话边界', () => {
  it('认证状态必须绑定身份，拒绝缺少CSRF令牌', () => {
    expect(() => parseSession({ authenticated: true, identity: null, csrf_token: 'token' })).toThrow()
    expect(() => parseSession({ authenticated: false, identity: null, csrf_token: '' })).toThrow()
    expect(parseSession({ authenticated: false, identity: null, csrf_token: 'token' }).authenticated).toBe(false)
  })
  it('写入绑定CSRF且失败后阻止重复提交，刷新后恢复', async () => {
    const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) => Promise.resolve(init.method === 'POST' ? new Response('', { status: 401 }) : new Response(JSON.stringify({ authenticated: false, identity: null, csrf_token: 'test-csrf' }))))
    vi.stubGlobal('fetch', fetcher)
    const changed = vi.fn(), scope = effectScope(), session = scope.run(() => useSession(changed))!
    await session.load()
    expect(await session.submit('login', 'user', 'secret')).toBe(false)
    expect(fetcher.mock.calls[1][1].headers['X-CSRF-Token']).toBe('test-csrf')
    const count = fetcher.mock.calls.length
    await session.submit('login', 'user', 'secret')
    expect(fetcher).toHaveBeenCalledTimes(count)
    expect(changed).not.toHaveBeenCalled()
    await session.load()
    expect(session.stale.value).toBe(false)
    scope.stop()
  })
})
