import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
test('服务运行筛选与分页，真实详情不回退演示', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-11', { recursive: true })
  const item = { id: 'RUN-1', title: '检查代码并等待审批', source: 'ledger', framework: 'LangGraph', tools: [], state: 'degraded', segment: 2, started_at: '2026-11-19T12:00:00Z', completed_at: null, parent_run_id: 'PARENT-1', child_run_ids: ['CHILD-1'], decision_count: 3 }
  await page.route('**/api/v27/runs?*', route => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor')
    return route.fulfill({ json: { source: 'ledger', items: cursor ? [{ ...item, id: 'RUN-2', title: '后续检查' }] : [item], next_cursor: cursor ? null : 'page2' } })
  })
  await page.route('**/api/v27/runs/RUN-1/audit', route => route.fulfill({ status: 403, json: {} }))
  await page.goto('/?runs=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  const list = page.locator('.service-run-history')
  await expect(list).toContainText('降级运行 · 第 2 段')
  await list.getByText('查看委派关系').click()
  await expect(list.getByRole('button', { name: 'CHILD-1', exact: true })).toBeVisible()
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await list.screenshot({ path: `../docs/reviews/ui/quality-round-11/runs-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.service-run-history').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  await list.getByRole('button', { name: '加载更多运行' }).click()
  await expect(list.locator('.history-card')).toHaveCount(2)
  await list.locator('.history-card').first().click()
  await expect(page.locator('.run-detail').getByRole('alert')).toContainText('无权')
  await expect(page.getByLabel('数据来源')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '回到任务' })).toHaveCount(0)
})

for (const status of [401, 403]) {
  test(`分页 ${status} 撤销可见列表与游标，重新查询从首屏重新授权`, async ({ page }) => {
    const item = { id: 'RUN-VISIBLE', title: '原有授权记录', source: 'ledger', framework: 'LangGraph', tools: [], state: 'open', segment: 1, started_at: '2026-11-19T12:00:00Z', completed_at: null, parent_run_id: null, child_run_ids: [], decision_count: 0 }
    let pageStatus = 503
    const queries: URLSearchParams[] = []
    await page.route('**/api/v27/runs?*', route => {
      const params = new URL(route.request().url()).searchParams; queries.push(params)
      return params.has('cursor') ? route.fulfill({ status: pageStatus, json: {} }) : route.fulfill({ json: { source: 'ledger', items: [item], next_cursor: 'page2' } })
    })
    await page.goto('/#section=运行记录&run_source=api')
    const list = page.getByLabel('账本运行查询', { exact: true })
    await expect(list.locator('.history-card')).toHaveCount(1)
    await list.getByRole('button', { name: '加载更多运行' }).click()
    await expect(list.getByRole('alert')).toContainText('暂时不可用')
    await expect(list.locator('.history-card')).toHaveCount(1)
    await list.getByRole('button', { name: '重新查询' }).click()
    await expect(list.getByRole('alert')).toHaveCount(0)
    pageStatus = status
    await list.getByRole('button', { name: '加载更多运行' }).click()
    await expect(list.getByRole('alert')).toContainText(status === 401 ? '登录已失效' : '无权')
    await expect(list.locator('.history-card')).toHaveCount(0)
    await expect(list.getByRole('button', { name: '加载更多运行' })).toHaveCount(0)
    if (status === 403) {
      await mkdir('../docs/reviews/ui/quality-round-37', { recursive: true })
      for (const width of [1440, 1024, 390, 320]) {
        await page.setViewportSize({ width, height: 900 })
        await list.screenshot({ path: `../docs/reviews/ui/quality-round-37/pagination-${width}.png`, animations: 'disabled' })
      }
    }
    await list.getByRole('button', { name: '重新查询' }).click()
    await expect(list.locator('.history-card')).toHaveCount(1)
    expect(queries[queries.length - 1]!.has('cursor')).toBe(false)
  })
}
