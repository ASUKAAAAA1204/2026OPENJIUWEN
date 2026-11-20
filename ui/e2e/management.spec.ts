import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { evaluationFixture } from '../tests/fixtures/evaluations'
import { createManagementDemo } from '../src/components/workbench/management'
import { authenticatedSession } from './helpers/authenticatedSession'
import { inspectState } from './helpers/stateA11y'
test.beforeEach(async ({ page }) => authenticatedSession(page))

test('发布通过仍注明未发布，报价需范围确认且零条目有明确空态', async ({ page }) => {
  const config = { ...createManagementDemo(), source: 'ledger' }
  config.capabilities.release_gate.enabled = true
  config.capabilities.remediation_quote.enabled = true
  let quotes = 0
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  await page.route('**/api/v27/management', route => route.fulfill({ json: config }))
  await page.route('**/api/v27/management/assess-release', route => {
    const body = route.request().postDataJSON()
    return route.fulfill({ json: { target: body.target, assessment_id: 'PASS-54', assessed_at: '2026-11-19T12:00:00Z', verdict: 'PASS', reasons: ['合成响应：满足版本发布规则'], checks: [{ name: '证据完整率', passed: true, actual: '100%', required: '90%' }] } })
  })
  await page.route('**/api/v27/management/quote', route => {
    quotes++
    const body = route.request().postDataJSON()
    return route.fulfill({ json: { target: body.target, quote_id: 'EMPTY-54', created_at: '2026-11-19T12:00:00Z', currency: 'CNY', total_minor: 0, items: [] } })
  })
  await page.goto('/?evaluation=api#section=版本评估')
  const manager = page.locator('.evaluation-management')
  await expect(manager.getByRole('button', { name: '获取逐条整改报价' })).toBeDisabled()
  expect(quotes).toBe(0)
  await manager.getByLabel('我已核对所选版本、策略和当前评估快照。').check()
  await manager.getByRole('button', { name: '重新核验发布条件' }).click()
  await expect(manager.locator('.management-result')).toContainText('通过条件核验')
  await expect(manager.locator('.management-result')).toContainText('未执行发布')
  await inspectState(page, 'release-passed-not-published', '.evaluation-management', 71)
  await manager.getByRole('button', { name: '获取逐条整改报价' }).click()
  await expect(manager.locator('.management-result')).toContainText('零估价不代表所有证据或发布条件均已通过')
  await expect(manager.locator('.management-result')).toContainText('估价合计 ¥0.00')
  expect(quotes).toBe(1)
  await inspectState(page, 'quote-empty', '.evaluation-management', 71)
  await mkdir('../docs/reviews/ui/quality-round-54', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await manager.screenshot({ path: `../docs/reviews/ui/quality-round-54/quote-empty-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.evaluation-management').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
})

test('本地管理启用留痕、阻断评估与逐条报价', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-8', { recursive: true })
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  const manager = page.locator('.evaluation-management')
  await expect(manager.locator('.management-capabilities')).toContainText('未启用')
  await manager.getByLabel('变更原因').fill('核对本地演示配置')
  await manager.getByLabel('我已核对版本、策略与变更影响。').check()
  await manager.getByRole('button', { name: '启用发布闸', exact: true }).click()
  await manager.getByText('最近一次配置审计').click()
  await expect(manager).toContainText('LOCAL-DEMO-USER')
  await manager.getByLabel('我已核对所选版本、策略和当前评估快照。').check()
  await manager.getByRole('button', { name: '重新核验发布条件' }).click()
  await expect(manager.locator('.management-result')).toContainText('阻断')
  await manager.getByLabel('变更原因').fill('验证演示报价')
  await manager.getByLabel('我已核对版本、策略与变更影响。').check()
  await manager.getByRole('button', { name: '启用整改报价', exact: true }).click()
  await manager.getByLabel('我已核对所选版本、策略和当前评估快照。').check()
  await manager.getByRole('button', { name: '获取逐条整改报价' }).click()
  await expect(manager.locator('.management-result')).toContainText('¥200.00')
  await inspectState(page, 'quote-items', '.evaluation-management', 71)
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await manager.screenshot({ path: `../docs/reviews/ui/quality-round-8/management-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.evaluation-management').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
})

test('配置冲突需刷新，服务判定绑定评估快照且不执行发布', async ({ page }) => {
  const config = { ...createManagementDemo(), source: 'ledger' }
  config.capabilities.version_comparison.enabled = true
  let conflict = true, posts = 0
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  await page.route('**/api/v27/management', route => route.fulfill({ json: config }))
  await page.route('**/api/v27/management/configure', async route => {
    posts++
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    const body = route.request().postDataJSON()
    expect(body.expected_revision).toBe(config.revision)
    expect(route.request().headers()['idempotency-key']).toBe(body.request_id)
    if (conflict) return route.fulfill({ status: 409, json: {} })
    config.revision = 'server-2'; config.capabilities.release_gate.enabled = true
    config.last_change = { actor_id: 'admin', at: '2026-11-19T12:00:00Z', reason: body.reason, audit_id: 'AUDIT-2' }
    return route.fulfill({ json: config })
  })
  await page.route('**/api/v27/management/assess-release', route => {
    const body = route.request().postDataJSON()
    expect(body.target.evaluation_revision).toBe('fixture-1')
    return route.fulfill({ json: { target: body.target, assessment_id: 'ASSESSMENT-1', assessed_at: '2026-11-19T12:00:00Z', verdict: 'BLOCK', reasons: ['证据完整率未满足发布规则'], checks: [{ name: '证据完整率', passed: false, actual: '80%', required: '90%' }] } })
  })
  await page.goto('/?evaluation=api')
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  const manager = page.locator('.evaluation-management')
  await manager.getByLabel('变更原因').fill('开启规则评估')
  await manager.getByLabel('我已核对版本、策略与变更影响。').check()
  await manager.getByRole('button', { name: '启用发布闸', exact: true }).click()
  await expect(manager.getByRole('alert')).toContainText('变化')
  await inspectState(page, 'management-conflict', '.evaluation-management')
  expect(posts).toBe(1)
  conflict = false
  await manager.getByRole('button', { name: '刷新并确认状态' }).click()
  await expect(manager.getByLabel('变更原因')).toHaveValue('')
  await manager.getByLabel('变更原因').fill('重新核对启用依据')
  await manager.getByLabel('我已核对版本、策略与变更影响。').check()
  await manager.getByRole('button', { name: '启用发布闸', exact: true }).click()
  await manager.getByLabel('我已核对所选版本、策略和当前评估快照。').check()
  await manager.getByRole('button', { name: '重新核验发布条件' }).click()
  await expect(manager.locator('.management-result')).toContainText('80% / 要求 90%')
  await expect(manager.locator('.management-result')).toContainText('未执行发布')
  await inspectState(page, 'release-blocked', '.evaluation-management')
  await manager.screenshot({ path: '../docs/reviews/ui/quality-round-8/release-block-1280.png', animations: 'disabled' })
})
