import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { settingsPreview } from '../src/components/workbench/settings'
import { inspectState } from './helpers/stateA11y'
test('登录失败清空密码，重新读取后登录并退出清理页面', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-10', { recursive: true })
  let authenticated = false, denied = true
  const snapshot = () => ({ authenticated, csrf_token: 'fixture-csrf', identity: authenticated ? { user_id: 'USER-1', display_name: '本机开发者', role: '开发者', scope: '本人运行' } : null })
  await page.route('**/api/v27/settings', route => route.fulfill({ json: { ...settingsPreview(), source: 'ledger' } }))
  await page.route('**/api/v27/session', route => route.fulfill({ json: snapshot() }))
  await page.route('**/api/v27/session/login', route => {
    expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf')
    if (denied) return route.fulfill({ status: 401, json: {} })
    authenticated = true
    return route.fulfill({ json: snapshot() })
  })
  await page.route('**/api/v27/session/logout', route => { authenticated = false; return route.fulfill({ json: snapshot() }) })
  await page.goto('/?settings=api')
  await page.getByRole('button', { name: '⚙ 设置' }).click()
  const panel = page.locator('.session-panel')
  await panel.getByLabel('账号', { exact: true }).fill('local-user')
  await panel.getByLabel('密码', { exact: true }).fill('test-secret')
  await inspectState(page, 'session-login', '.session-panel')
  await panel.getByRole('button', { name: '登录本机账号' }).click()
  await expect(panel.getByRole('alert')).toContainText('重新读取')
  await expect(panel.getByLabel('密码', { exact: true })).toHaveCount(0)
  await inspectState(page, 'session-denied', '.session-panel')
  denied = false
  await panel.getByRole('button', { name: '重新读取会话' }).click()
  await panel.getByLabel('账号', { exact: true }).fill('local-user')
  await expect(panel.getByLabel('密码', { exact: true })).toHaveValue('')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-10/login-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.session-panel').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  await panel.getByLabel('密码', { exact: true }).fill('test-secret')
  await panel.getByRole('button', { name: '登录本机账号' }).click()
  await expect(page).toHaveTitle('设置 · TemisGuard')
  await expect(panel).toContainText('本机开发者')
  await inspectState(page, 'session-authenticated', '.session-panel')
  await panel.getByLabel('确认退出并清除当前页面的账号数据').check()
  await panel.getByRole('button', { name: '退出本机账号' }).click()
  await expect(page).toHaveTitle('设置 · TemisGuard')
  await expect(panel.getByLabel('账号', { exact: true })).toBeVisible()
  await expect(panel.getByLabel('账号', { exact: true })).toHaveValue('')
  await expect(panel.getByLabel('密码', { exact: true })).toHaveValue('')
  await expect(panel).not.toContainText('本机开发者')
  await inspectState(page, 'session-logged-out', '.session-panel', 71)
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('test-secret')
})
