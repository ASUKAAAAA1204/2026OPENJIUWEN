import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

test('列表前进后退恢复来源与旧筛选并重新查询，地址不包含搜索文本', async ({ page }) => {
  const reads: string[] = []
  await page.route('**/api/v27/runs?*', route => { reads.push(route.request().url()); return route.fulfill({ json: { source: 'ledger', items: [], next_cursor: null } }) })
  await page.goto('/#section=运行记录&run_source=api&run_framework=LangGraph&run_state=closed')
  const list = page.getByLabel('账本运行查询', { exact: true })
  await expect(list.getByLabel('服务运行框架')).toHaveValue('LangGraph')
  await expect(list.getByRole('button', { name: '查询运行', exact: true })).toBeEnabled()
  await page.evaluate(() => { history.pushState(null, '', '#section=运行记录&run_source=api&run_framework=openJiuwen&run_state=open'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(list.getByLabel('服务运行框架')).toHaveValue('openJiuwen')
  await expect.poll(() => new URL(reads[reads.length - 1]!).searchParams.get('state')).toBe('open')
  await page.goBack()
  await expect(list.getByLabel('服务运行框架')).toHaveValue('LangGraph')
  await expect.poll(() => new URL(reads[reads.length - 1]!).searchParams.get('state')).toBe('closed')
  await page.goForward()
  await expect(list.getByLabel('服务运行框架')).toHaveValue('openJiuwen')
  await expect(list.getByRole('button', { name: '查询运行', exact: true })).toBeEnabled()
  await page.evaluate(() => { history.pushState(null, '', '#section=运行记录&run_source=preview'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByLabel('运行来源')).toHaveValue('preview')
  await page.goBack()
  await expect(page.getByLabel('运行来源')).toHaveValue('api')
  await expect(list.getByLabel('服务运行框架')).toHaveValue('openJiuwen')
  await expect(list.getByLabel('生命周期')).toHaveValue('open')
  await mkdir('../docs/reviews/ui/quality-round-36', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await list.screenshot({ path: `../docs/reviews/ui/quality-round-36/history-${width}.png`, animations: 'disabled' })
  }
})
