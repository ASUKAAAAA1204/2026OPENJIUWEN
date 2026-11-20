import { describe, expect, it, afterEach, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import { createManagementDemo, parseManagement, parseQuote, parseRelease, useManagement, type ManagementTarget } from '../src/components/workbench/management'
const target: ManagementTarget = { skill_id: 'skill', skill_version: '1', policy_version: 'policy', evaluation_revision: 'eval-1' }
afterEach(() => vi.unstubAllGlobals())
describe('管理配置、快照绑定与金额核验', () => {
  it('三项能力默认关闭，权限布尔不接受数字', () => {
    const data = createManagementDemo()
    expect(Object.values(data.capabilities).every(item => !item.enabled)).toBe(true)
    data.source = 'ledger'
    expect(parseManagement(data).revision).toBe('demo-1')
    data.capabilities.release_gate.can_configure = 1 as never
    expect(() => parseManagement(data)).toThrow()
  })
  it('发布判定必须绑定评估快照且与逐条检查一致', () => {
    const data = { target, assessment_id: 'assessment', assessed_at: '2026-11-19T12:00:00Z', verdict: 'PASS', reasons: ['满足条件'], checks: [{ name: '证据完整率', passed: false, actual: '80%', required: '90%' }] }
    expect(() => parseRelease(data, target)).toThrow()
    data.verdict = 'BLOCK'
    expect(parseRelease(data, target).verdict).toBe('BLOCK')
    expect(() => parseRelease(data, { ...target, evaluation_revision: 'other' })).toThrow()
  })
  it('逐项金额用分精确核对，拒绝重复项、负值和不匹配合计', () => {
    const data = { target, quote_id: 'quote', created_at: '2026-11-19T12:00:00Z', currency: 'CNY', total_minor: 1234, items: [{ item_id: 'item', decision_id: 'dec', action: '补证', basis: '规则1', amount_minor: 1234 }] }
    expect(parseQuote(data, target).total_minor).toBe(1234)
    data.items.push(data.items[0])
    expect(() => parseQuote(data, target)).toThrow()
    data.items.pop(); data.items[0].amount_minor = -1
    expect(() => parseQuote(data, target)).toThrow()
    data.items[0].amount_minor = 1000
    expect(() => parseQuote(data, target)).toThrow()
  })
  it('演示启用需确认与原因，保留操作人及审计记录', async () => {
    const scope = effectScope(), resource = scope.run(() => useManagement(ref('preview'), ref(target)))!
    await nextTick()
    expect(await resource.mutate('configure', 'release_gate', true, '', true)).toBe(false)
    expect(await resource.mutate('configure', 'release_gate', true, '仅验证演示', true)).toBe(true)
    expect(resource.data.value?.last_change?.actor_id).toBe('LOCAL-DEMO-USER')
    expect(await resource.mutate('release', undefined, undefined, '', true)).toBe(true)
    expect(resource.release.value?.verdict).toBe('BLOCK')
    scope.stop()
  })
  it('服务写入冲突后阻止重试，刷新才能重新核对', async () => {
    const data = { ...createManagementDemo(), source: 'ledger' }
    const fetcher = vi.fn().mockImplementation((url: string, init?: RequestInit) => Promise.resolve(url === '/api/v27/session' ? new Response(JSON.stringify({ authenticated: true, csrf_token: 'test-csrf', identity: { user_id: 'admin', display_name: 'admin', role: '平台管理员', scope: 'test' } })) : init?.method === 'POST' ? new Response('', { status: 409 }) : new Response(JSON.stringify(data))))
    vi.stubGlobal('fetch', fetcher)
    const scope = effectScope(), resource = scope.run(() => useManagement(ref('api'), ref(target)))!
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(await resource.mutate('configure', 'release_gate', true, '启用核验', true)).toBe(false)
    expect(resource.stale.value).toBe(true)
    const count = fetcher.mock.calls.length
    expect(await resource.mutate('configure', 'release_gate', true, '启用核验', true)).toBe(false)
    expect(fetcher).toHaveBeenCalledTimes(count)
    await resource.load()
    expect(resource.stale.value).toBe(false)
    scope.stop()
  })
})
