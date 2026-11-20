import { workbenchApi } from './helpers/workbenchApi'
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'

test.beforeEach(async ({ page }) => workbenchApi(page))

test('工作台导航、通知、键盘和减少动态效果', async ({ page }) => {
  await page.goto('/')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: '跳转到工作区' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#workspace-content')).toBeFocused()
  await expect(page.locator('.run-create-form')).toBeVisible()
  await expect(page.locator('.source-picker')).toHaveCount(0)
  await page.getByRole('button', { name: '运行记录', exact: false }).click()
  await expect(page).toHaveTitle('运行记录 · TemisGuard')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect(page.locator('.task-workspace:visible')).toHaveCSS('animation-name', 'none')
})

test('工作台来源刷新恢复，任务草稿与确认不进入链接', async ({ page }) => {
  await page.goto('/')

  await page.reload()
  await expect(page.getByRole('combobox', { name: '任务来源', exact: true })).toHaveCount(0)
  await page.goto('/#section=工作台&workspace_source=api&task=secret&confirmed=true')
  await expect(page.getByRole('combobox', { name: '任务来源', exact: true })).toHaveCount(0)
  expect(page.url()).not.toContain('task=secret')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.goBack()
  await expect(page.getByRole('combobox', { name: '任务来源', exact: true })).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-61', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.task-workspace:visible').screenshot({ path: `../docs/reviews/ui/quality-round-61/workspace-source-${width}.png`, animations: 'disabled' })
  }
})

test('全页面响应式截图与严重无障碍检查', async ({ page }) => {
  test.setTimeout(90000)
  await mkdir('../docs/reviews/ui/quality-round-1', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    for (const [index, name] of ['workspace', 'history', 'approval', 'debt', 'evaluation'].entries()) {
      await page.getByRole('navigation', { name: '主导航' }).getByRole('button').nth(index).click()
      await expect(page.locator('.task-workspace:visible')).toHaveCSS('opacity', '1')
      await page.evaluate(() => document.fonts.ready)
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: `../docs/reviews/ui/quality-round-1/${name}-${width}.png`, fullPage: true, animations: 'disabled' })
      const result = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
      expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? '')), `${name} at ${width}px`).toEqual([])
    }
    await page.getByRole('button', { name: '⚙ 设置' }).click()
    await expect(page.locator('.task-workspace:visible')).toHaveCSS('opacity', '1')
    await page.evaluate(() => document.fonts.ready)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-1/settings-${width}.png`, fullPage: true, animations: 'disabled' })
    const settingsAccessibility = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
    expect(settingsAccessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? '')), `settings at ${width}px`).toEqual([])
  }
})

test('运行详情筛选、证据边界、下载与回放条件', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-2', { recursive: true })
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await expect(page.locator('.inspector:visible')).toHaveCount(0)
  await page.locator('.run-history .history-card').first().click()
  await expect(page.getByRole('heading', { name: '运行详情', exact: true })).toBeVisible()
  await page.getByLabel('Gate 判定', { exact: true }).selectOption('DENY')
  await expect(page.getByRole('heading', { name: '没有匹配的决策' })).toBeVisible()
  await page.getByRole('button', { name: '清除筛选' }).click()
  await page.getByRole('button', { name: '证据与账本', exact: true }).click()
  await expect(page.getByRole('heading', { name: '尚无可核验证据' })).toBeVisible()
  await expect(page.getByText('授权导出 JSONL', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '回放', exact: true }).click()
  await expect(page.getByRole('region', { name: '回放', exact: true })).toBeVisible()
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    for (const [name, label] of [['decision', '判定与参数'], ['evidence', '证据与账本'], ['replay', '回放']]) {
      await page.getByRole('button', { name: label, exact: true }).click()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: `../docs/reviews/ui/quality-round-2/${name}-${width}.png`, fullPage: true, animations: 'disabled' })
    }
  }
  const result = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  await page.getByRole('button', { name: '← 返回运行记录' }).click()
  await expect(page.getByRole('heading', { name: '运行记录', exact: true })).toBeVisible()
})
