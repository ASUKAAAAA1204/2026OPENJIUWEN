import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { settingsPreview } from '../src/components/workbench/settings'

test('设置分类与手动选择来源刷新恢复，非法分类安全回退', async ({ page }) => {
  await page.route('**/api/v27/settings', route => route.fulfill({ json: { ...settingsPreview(), source: 'ledger' } }))
  await page.goto('/#section=设置')
  const panel = page.locator('.settings-panel')
  await panel.getByRole('combobox', { name: '配置来源' }).selectOption('api')
  await panel.getByRole('button', { name: '框架接入', exact: true }).click()
  await page.reload()
  await expect(panel.getByRole('combobox', { name: '配置来源' })).toHaveValue('api')
  await expect(panel.getByRole('button', { name: '框架接入', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(panel).toContainText('服务端配置')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.getByRole('button', { name: '⚙ 设置' }).click()
  await expect(panel.getByRole('button', { name: '框架接入', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.reload()
  await expect(panel.getByRole('button', { name: '框架接入', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await expect(page).toHaveTitle('运行记录 · TemisGuard')
  await page.goBack()
  await expect(panel.getByRole('button', { name: '框架接入', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(panel.getByRole('combobox', { name: '配置来源' })).toHaveValue('api')
  await mkdir('../docs/reviews/ui/quality-round-58', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-58/settings-restored-${width}.png`, animations: 'disabled' })
  }
  await page.goto('/#section=设置&settings_category=unknown&settings_source=preview')
  await expect(panel.getByRole('button', { name: '身份与权限', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(panel.getByRole('combobox', { name: '配置来源' })).toHaveValue('preview')
})
test('配置身份范围、规则版本与接入状态按服务快照显示', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-9', { recursive: true })
  const data = { ...settingsPreview(), source: 'ledger', revision: 'settings-1', identity: { user_id: 'USER-DEV', display_name: '开发人员', role: '开发者', scope: '仅本人运行' }, policy: { version: 'policy-2.7', content_hash: 'a'.repeat(64), rules: [{ decision_class: 'FILE_WRITE', gate: 'ASK', exec_policy: 'normal', risk: 'high', ask_timeout_seconds: 30, ask_timeout_policy: 'DENY' }], history: [{ version: 'policy-2.7', actor_id: 'admin', at: '2026-11-19T12:00:00Z', reason: '冻结高危写入规则' }] } }
  await page.route('**/api/v27/settings', route => route.fulfill({ json: data }))
  await page.route('**/api/v27/session', route => route.fulfill({ json: { authenticated: false, csrf_token: 'fixture-csrf', identity: null } }))
  await page.goto('/?settings=api')
  await page.getByRole('button', { name: '⚙ 设置' }).click()
  const panel = page.locator('.settings-panel')
  await expect(panel).toContainText('仅本人运行')
  await panel.getByText('查看 PRD v2.7 角色权限基准').click()
  await expect(panel.getByRole('table')).toContainText('所属 Skill')
  await panel.getByRole('button', { name: '策略表', exact: true }).click()
  await expect(panel).toContainText('等待 30 秒 · 超时 DENY')
  await panel.getByText('查看版本变更记录').click()
  await expect(panel).toContainText('冻结高危写入规则')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-9/policy-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.settings-panel').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  await panel.getByRole('button', { name: '框架接入', exact: true }).click()
  await expect(panel).toContainText('OpenAI Agents SDK')
  await expect(panel).toContainText('未验证')
})
test('无权限不会回退预览，重试后可恢复真实空配置', async ({ page }) => {
  let denied = true
  await page.route('**/api/v27/settings', route => route.fulfill(denied ? { status: 403, json: {} } : { json: { ...settingsPreview(), source: 'ledger' } }))
  await page.route('**/api/v27/session', route => route.fulfill({ json: { authenticated: false, csrf_token: 'fixture-csrf', identity: null } }))
  await page.goto('/?settings=api')
  await page.getByRole('button', { name: '⚙ 设置' }).click()
  const panel = page.locator('.settings-panel')
  await expect(panel.getByRole('alert')).toContainText('无权')
  await expect(panel.locator('.approval-filters')).toHaveCount(0)
  denied = false
  await panel.getByRole('button', { name: '重新读取配置' }).click()
  await expect(panel).toContainText('尚无已认证身份')
})

test('白名单能力与三框架状态按快照展示，不把选择器当接入证明', async ({ page }) => {
  const data = { ...settingsPreview(), source: 'ledger', whitelist: { version: 'WL-52', classes: ['FILE_READ', 'FILE_WRITE', 'COMMAND_EXEC'], tools: [
    { name: 'read_file', capability: 'read_only', workspace: 'workspace/repository' },
    { name: 'shell', capability: 'mixed', workspace: 'workspace/repository' },
    { name: 'write_file', capability: 'write', workspace: 'workspace/repository' },
  ] }, frameworks: [
    { name: 'openJiuwen', version: '0.1.20.post1', status: 'verified', checked_at: '2026-11-19T12:00:00Z', reason: '合成服务核验快照' },
    { name: 'LangGraph', version: null, status: 'unavailable', checked_at: null, reason: '当前环境未安装' },
    { name: 'OpenAI Agents SDK', version: null, status: 'unverified', checked_at: null, reason: '尚未核验' },
  ] }
  await page.route('**/api/v27/settings', route => route.fulfill({ json: data }))
  await page.goto('/?settings=api')
  await page.getByRole('button', { name: '⚙ 设置' }).click()
  const panel = page.locator('.settings-panel')
  await panel.getByRole('button', { name: '白名单与工具', exact: true }).click()
  await expect(panel).toContainText('白名单 WL-52 · 覆盖 3 / 11 类')
  for (const capability of ['read_only', 'mixed', 'write']) await expect(panel.locator('.settings-row').filter({ hasText: capability }).first()).toBeVisible()
  await panel.getByRole('button', { name: '框架接入', exact: true }).click()
  await expect(panel.locator('.settings-row').filter({ hasText: 'openJiuwen' })).toContainText('已验证 · 0.1.20.post1')
  await expect(panel.locator('.settings-row').filter({ hasText: 'LangGraph' })).toContainText('不可用')
  await expect(panel.locator('.settings-row').filter({ hasText: 'OpenAI Agents SDK' })).toContainText('未验证')
  await mkdir('../docs/reviews/ui/quality-round-52', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-52/frameworks-${width}.png`, animations: 'disabled' })
  }
})
