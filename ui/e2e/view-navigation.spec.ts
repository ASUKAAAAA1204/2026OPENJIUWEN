import { workbenchApi } from './helpers/workbenchApi'
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
test.beforeEach(async ({ page }) => workbenchApi(page))

test('检查器和决策详情支持方向键、首尾键与单一Tab入口', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  const toolbar = page.getByRole('toolbar', { name: '详情视图' })
  await toolbar.getByRole('button', { name: '判定与参数' }).focus()
  await page.keyboard.press('End')
  await expect(toolbar.getByRole('button', { name: '回放', exact: true })).toBeFocused()
  await expect(page.getByRole('region', { name: '回放', exact: true })).toContainText('服务端回放')
  await expect(toolbar.locator('button[tabindex="0"]')).toHaveCount(1)
  await page.keyboard.press('Home')
  await page.keyboard.press('ArrowLeft')
  await expect(toolbar.getByRole('button', { name: '回放', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowLeft')
  await expect(toolbar.getByRole('button', { name: '证据与账本' })).toBeFocused()
  await expect(page.getByRole('region', { name: '证据与账本', exact: true })).toContainText('尚无可核验证据')
  await mkdir('../docs/reviews/ui/quality-round-23', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.audit-detail').screenshot({ path: `../docs/reviews/ui/quality-round-23/keyboard-${width}.png`, animations: 'disabled' })
  }
})
