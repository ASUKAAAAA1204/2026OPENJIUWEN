import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

test('直接修改片段恢复板块及同页分类，后退还原分类', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => { location.hash = new URLSearchParams({ section: '设置', settings_category: '框架接入', settings_source: 'preview' }).toString() })
  await expect(page).toHaveTitle('设置 · TemisGuard')
  await expect(page.getByRole('button', { name: '框架接入', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.evaluate(() => { location.hash = new URLSearchParams({ section: '设置', settings_category: '策略表', settings_source: 'preview' }).toString() })
  await expect(page.getByRole('button', { name: '策略表', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.goBack()
  await expect(page.getByRole('button', { name: '框架接入', exact: true })).toHaveAttribute('aria-pressed', 'true')
})

test('六板块刷新保留位置，前进后退不新增历史，未知位置安全返回工作台', async ({ page }) => {
  await page.goto('/')
  for (const name of ['运行记录', '人工审批', '证据与债务', '版本评估', '设置']) {
    await page.getByRole('button', { name: name === '设置' ? '⚙ 设置' : new RegExp(name), exact: name === '设置' }).first().click()
    await expect(page).toHaveTitle(`${name} · TemisGuard`)
    expect(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('section')).toBe(name)
    await page.reload()
    await expect(page).toHaveTitle(`${name} · TemisGuard`)
  }
  await page.goBack()
  await expect(page).toHaveTitle('版本评估 · TemisGuard')
  await page.goBack()
  await expect(page).toHaveTitle('证据与债务 · TemisGuard')
  await page.goForward()
  await expect(page).toHaveTitle('版本评估 · TemisGuard')
  await page.goto('/#section=unknown')
  await expect(page).toHaveTitle('工作台 · TemisGuard')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.goBack()
  await expect(page).toHaveTitle('工作台 · TemisGuard')
  await page.goForward()
  await expect(page).toHaveTitle('运行记录 · TemisGuard')
  await mkdir('../docs/reviews/ui/quality-round-19', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.reload()
    await expect(page).toHaveTitle('运行记录 · TemisGuard')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-19/navigation-${width}.png`, fullPage: true, animations: 'disabled' })
  }
})
