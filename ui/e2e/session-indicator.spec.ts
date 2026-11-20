import { runContextFixture } from '../tests/fixtures/lifecycle'
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { testSession } from './helpers/authenticatedSession'

test('全局身份明确认证与可见范围，四档布局、键盘进入设置', async ({ page }) => {
  await page.route('**/api/v27/session', route => route.fulfill({ json: { ...testSession, identity: { ...testSession.identity, display_name: '需要完整保留的较长账号显示名称' } } }))
  await page.goto('/')
  const indicator = page.getByLabel('全局会话状态', { exact: true })
  await expect(indicator).toContainText('平台管理员')
  await expect(indicator.locator('.session-identity')).toHaveAccessibleDescription(/test-workspace/)
  await expect(indicator).not.toContainText('test-only-csrf')
  await mkdir('../docs/reviews/ui/quality-round-20', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-20/identity-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.workbench-topbar').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  await indicator.locator('.session-identity').focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveTitle('设置 · TemisGuard')
})

test('无法确认不冒充登录，重试可恢复匿名会话', async ({ page }) => {
  let available = false
  await page.route('**/api/v27/session', route => available ? route.fulfill({ json: { authenticated: false, csrf_token: 'anonymous-only', identity: null } }) : route.fulfill({ status: 403, json: {} }))
  await page.goto('/')
  const indicator = page.getByLabel('全局会话状态', { exact: true })
  await expect(indicator).toContainText('会话未确认')
  await expect(indicator.locator('.session-identity')).toHaveAccessibleDescription(/校验失败/)
  available = true
  await indicator.getByRole('button', { name: '刷新全局会话状态' }).click()
  await expect(indicator).toContainText('未登录')
  await expect(indicator.locator('.is-authenticated')).toHaveCount(0)
})

test('确认账号或权限变更后重新加载，清除原任务草稿', async ({ page }) => {
  let identity = testSession.identity
  await page.route('**/api/v27/session', route => route.fulfill({ json: { ...testSession, identity } }))
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: runContextFixture() }))
  await page.goto('/')
  const indicator = page.getByLabel('全局会话状态', { exact: true })
  await expect(indicator).toContainText('平台管理员')
  await page.getByLabel('任务描述').fill('属于旧账号的草稿内容')
  identity = { ...identity, user_id: 'second-user', display_name: '另一位用户', role: '开发者' }
  await indicator.getByRole('button', { name: '刷新全局会话状态' }).click()
  await expect(indicator).toContainText('另一位用户')
  await expect(page.getByLabel('任务描述')).toHaveValue('')
  await expect(indicator).not.toContainText('平台管理员')
})
