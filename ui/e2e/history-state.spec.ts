import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

test('运行详情与板块往返保留查询、来源、分页快照，主动查询获取新权限', async ({ page }) => {
  let reads = 0, denied = false
  const item = { id: 'RUN-STATE-1', title: '需要保留的查询结果', source: 'ledger', framework: 'LangGraph', tools: [], state: 'closed', segment: 1, started_at: '2026-11-19T12:00:00Z', completed_at: '2026-11-19T12:01:00Z', parent_run_id: null, child_run_ids: [], decision_count: 0 }
  await page.route('**/api/v27/runs?*', route => {
    reads++
    if (denied) return route.fulfill({ status: 403, json: {} })
    const cursor = new URL(route.request().url()).searchParams.get('cursor')
    return route.fulfill({ json: { source: 'ledger', items: [{ ...item, id: cursor ? 'RUN-STATE-2' : item.id }], next_cursor: cursor ? null : 'next' } })
  })
  await page.route('**/api/v27/runs/*/audit', route => route.fulfill({ status: 403, json: {} }))
  await page.goto('/?runs=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  const list = page.locator('.service-run-history')
  await expect(list.locator('.history-card')).toHaveCount(1)
  await list.getByLabel('查询运行', { exact: true }).fill('需要保留')
  await list.getByLabel('服务运行框架').selectOption('LangGraph')
  await list.getByLabel('生命周期').selectOption('closed')
  await list.getByRole('button', { name: '查询运行', exact: true }).click()
  await list.getByRole('button', { name: '加载更多运行' }).click()
  await expect(list.locator('.history-card')).toHaveCount(2)
  const before = reads
  await list.locator('.history-card').nth(1).click()
  await expect(page.getByRole('heading', { name: '访问受限' })).toBeVisible()
  await page.getByRole('button', { name: '← 返回运行记录' }).click()
  await expect(list.getByLabel('查询运行', { exact: true })).toHaveValue('需要保留')
  await expect(list.getByLabel('服务运行框架')).toHaveValue('LangGraph')
  await expect(list.getByLabel('生命周期')).toHaveValue('closed')
  await expect(list.locator('.history-card')).toHaveCount(2)
  await page.getByRole('navigation').getByRole('button').nth(2).click()
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await expect(list.locator('.history-card')).toHaveCount(2)
  expect(reads).toBe(before)
  await expect(list).toContainText('返回时保留本次查询快照')
  expect(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('run_framework')).toBe('LangGraph')
  await list.getByLabel('服务运行框架').selectOption('openJiuwen')
  expect(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('run_framework')).toBe('LangGraph')
  await list.getByLabel('服务运行框架').selectOption('LangGraph')
  await mkdir('../docs/reviews/ui/quality-round-26', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await list.screenshot({ path: `../docs/reviews/ui/quality-round-26/history-${width}.png`, animations: 'disabled' })
  }
  denied = true
  await list.getByRole('button', { name: '查询运行', exact: true }).click()
  await expect(list.getByRole('alert')).toContainText('无权')
  await expect(list.locator('.history-card')).toHaveCount(0)
  await page.reload()
  await expect(list.getByLabel('查询运行', { exact: true })).toHaveValue('')
  await expect(list.getByLabel('服务运行框架')).toHaveValue('LangGraph')
  await expect(list.getByLabel('生命周期')).toHaveValue('closed')
  await mkdir('../docs/reviews/ui/quality-round-35', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await list.screenshot({ path: `../docs/reviews/ui/quality-round-35/history-${width}.png`, animations: 'disabled' })
  }
})
