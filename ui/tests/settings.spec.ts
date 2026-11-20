import { describe, expect, it } from 'vitest'
import { parseSettings, settingsPreview } from '../src/components/workbench/settings'
describe('配置快照', () => {
  it('预览不赋予身份或虚构生效策略', () => {
    const data = settingsPreview()
    expect(data.identity).toBe(null)
    expect(data.policy).toBe(null)
    expect(data.frameworks.every(item => item.status === 'unverified')).toBe(true)
  })
  it('拒绝错误角色、重复框架与未经核验的成功', () => {
    const data = { ...settingsPreview(), source: 'ledger' }
    expect(parseSettings(data).identity).toBe(null)
    data.frameworks[0].status = 'verified'
    expect(() => parseSettings(data)).toThrow()
    data.frameworks[0].version = '0.1.20.post1'
    data.frameworks[0].checked_at = '2026-11-19T12:00:00Z'
    expect(parseSettings(data).frameworks[0].status).toBe('verified')
    data.frameworks[1] = data.frameworks[0]
    expect(() => parseSettings(data)).toThrow()
  })
  it('高危ASK超时不得返回放行配置', () => {
    const data = { ...settingsPreview(), source: 'ledger', policy: { version: 'p1', content_hash: 'a'.repeat(64), rules: [{ decision_class: 'FILE_WRITE', gate: 'ASK', exec_policy: 'normal', risk: 'high', ask_timeout_seconds: 30, ask_timeout_policy: 'ALLOW' }], history: [] } }
    expect(() => parseSettings(data)).toThrow()
    data.policy.rules[0].ask_timeout_policy = 'DENY'
    expect(parseSettings(data).policy?.rules[0].ask_timeout_policy).toBe('DENY')
  })
})
