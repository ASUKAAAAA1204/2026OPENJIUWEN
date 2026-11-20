import { describe, expect, it } from 'vitest'
import { matchesPolicySettings, parsePolicyCatalog, parsePolicySwitch, policyChanges, policyValue } from '../src/components/workbench/policies'
import { policyCatalogFixture, policyReceiptFixture, policySettingsFixture } from './fixtures/policies'
describe('只读策略版本与切换绑定', () => {
  it('拒绝重复版本、错误生效指针和高危超时放行', () => {
    const data = policyCatalogFixture()
    expect(parsePolicyCatalog(data).active?.version).toBe('policy-2.7.1')
    data.versions.push(data.versions[0]); expect(() => parsePolicyCatalog(data)).toThrow(); data.versions.pop()
    data.active!.content_hash = 'c'.repeat(64); expect(() => parsePolicyCatalog(data)).toThrow(); data.active!.content_hash = 'a'.repeat(64)
    data.versions[0].configuration.rules[0].ask_timeout_policy = 'ALLOW'; expect(() => parsePolicyCatalog(data)).toThrow()
  })
  it('目录与设置必须一致，规则列表顺序不影响投影比对', () => {
    const catalog = policyCatalogFixture(), policy = policySettingsFixture(catalog).policy!
    expect(matchesPolicySettings(catalog, policy)).toBe(true)
    policy.rules = [{ ...policy.rules[0], gate: 'DENY', ask_timeout_seconds: null, ask_timeout_policy: null }]
    expect(matchesPolicySettings(catalog, policy)).toBe(false)
    expect(matchesPolicySettings(catalog, null)).toBe(false)
  })
  it('差异包含逐字段规则和非规则配置，不把键顺序当内容变化', () => {
    const { versions } = policyCatalogFixture(), changes = policyChanges(versions[0], versions[1])
    expect(changes.map(item => item.field)).toContain('FILE_WRITE · Gate')
    expect(changes.find(item => item.field === '配置项 债务复核阈值')).toMatchObject({ before: '6', after: '4' })
    expect(changes.find(item => item.field === 'FILE_WRITE · 超时处置')?.after).toBe('不适用（非 ASK）')
    const reordered = structuredClone(versions[0]); reordered.configuration = Object.fromEntries(Object.entries(reordered.configuration).reverse()) as typeof reordered.configuration
    expect(policyChanges(versions[0], reordered)).toEqual([])
    expect(policyValue(null)).not.toBe(policyValue(undefined))
    delete reordered.configuration.redaction_version
    expect(policyChanges(versions[0], reordered).some(item => item.after === '已移除')).toBe(true)
    reordered.configuration.rules = []
    expect(policyChanges(versions[0], reordered).some(item => item.after === '规则移除 → 默认 DENY')).toBe(true)
  })
  it('正常切换与回滚只改生效指针，保留所有发布快照', () => {
    const previous = policyCatalogFixture(), receipt = policyReceiptFixture(previous)
    const switched = parsePolicySwitch(receipt, previous, previous.versions[1], 'request-1', '核对后切换')
    expect(switched.catalog.active?.version).toBe('policy-2.7.2')
    const rollback = policyReceiptFixture(switched.catalog, 'request-2', '回滚至原基线', 0)
    expect(parsePolicySwitch(rollback, switched.catalog, previous.versions[0], 'request-2', '回滚至原基线').catalog.active?.version).toBe('policy-2.7.1')
  })
  it('拒绝改写历史规则、错误请求或审计原因', () => {
    const before = policyCatalogFixture(), target = before.versions[1]
    let receipt = policyReceiptFixture(before)
    receipt.catalog.versions[0].configuration.debt_review_threshold = 99
    expect(() => parsePolicySwitch(receipt, before, target, 'request-1', '核对后切换')).toThrow()
    receipt = policyReceiptFixture(before)
    expect(() => parsePolicySwitch(receipt, before, target, 'another-request', '核对后切换')).toThrow()
    expect(() => parsePolicySwitch(receipt, before, target, 'request-1', '不同原因')).toThrow()
    receipt.audit.from_hash = 'c'.repeat(64)
    expect(() => parsePolicySwitch(receipt, before, target, 'request-1', '核对后切换')).toThrow()
  })
  it('不可启用或未发布的候选、无切换权限及过期目录不能确认成功', () => {
    const before = policyCatalogFixture(), receipt = policyReceiptFixture(before)
    before.can_switch = false
    expect(() => parsePolicySwitch(receipt, before, before.versions[1], 'request-1', '核对后切换')).toThrow()
    before.can_switch = true; before.versions[1].eligible = false
    expect(() => parsePolicySwitch(receipt, before, before.versions[1], 'request-1', '核对后切换')).toThrow()
    before.versions[1].eligible = true
    expect(() => parsePolicySwitch(receipt, before, { ...before.versions[1], version: 'unpublished' }, 'request-1', '核对后切换')).toThrow()
    receipt.catalog.revision = before.revision
    expect(() => parsePolicySwitch(receipt, before, before.versions[1], 'request-1', '核对后切换')).toThrow()
  })
})
