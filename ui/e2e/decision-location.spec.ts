import { workbenchApi } from './helpers/workbenchApi'
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'

test.beforeEach(async ({ page }) => workbenchApi(page))

test('决策与视图刷新恢复，不存在的决策链接禁止误选首条', async ({ page }) => {
  const runId = 'RUN-LOCATION'
  const decisions = previewDecisions(createPreview(runId, 'location', 'openJiuwen')).map((item, index) => ({ ...item, decision_id: `LOC-${index}` }))
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'fixture' } } }))
  await page.goto(`/#run=${runId}&framework=openJiuwen&decision=LOC-1&view=${encodeURIComponent('证据与账本')}`)
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('LOC-1')
  await expect(page.getByRole('region', { name: '证据与账本', exact: true })).toBeVisible()
  await page.getByLabel('搜索决策', { exact: true }).fill('LOC-0')
  await expect(page.getByRole('heading', { name: '定位的决策被筛选隐藏' })).toBeVisible()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toHaveCount(0)
  await page.getByRole('button', { name: '清除筛选并显示定位决策', exact: true }).click()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('LOC-1')
  await page.reload()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('LOC-1')
  await expect(page.getByRole('region', { name: '证据与账本', exact: true })).toBeVisible()
  await page.locator('.decision-row').first().click()
  await page.getByRole('button', { name: '判定与参数', exact: true }).click()
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.slice(1)).get('decision')).toBe('LOC-0')
  await page.reload()
  await expect(page.getByRole('region', { name: '判定与参数', exact: true })).toContainText('LOC-0')
  await page.goto(`/#run=${runId}&framework=openJiuwen&decision=MISSING&view=unknown`)
  await expect(page.getByRole('heading', { name: '未找到链接中的决策' })).toBeVisible()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-33', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByLabel('决策详情', { exact: true }).screenshot({ path: `../docs/reviews/ui/quality-round-33/location-${width}.png`, animations: 'disabled' })
  }
  await page.getByRole('button', { name: '清除决策定位', exact: true }).click()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('LOC-0')
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.slice(1)).has('decision')).toBe(false)
})

test('同运行历史位置与预览切回服务恢复决策视图，不把预览位置写入账本链接', async ({ page }) => {
  const runId = 'RUN-DEMO-001'
  const decisions = previewDecisions(createPreview(runId, 'location', 'openJiuwen')).map((item, index) => ({ ...item, decision_id: `SERVICE-${index}` }))
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'fixture' } } }))
  await page.goto('/?audit=api')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  await page.locator('.decision-row').last().click()
  await page.getByRole('button', { name: '证据与账本', exact: true }).click()
  await page.reload()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('SERVICE-1')
  await expect(page.getByRole('region', { name: '证据与账本', exact: true })).toBeVisible()
  await page.evaluate(() => { history.pushState(null, '', location.href.replace('decision=SERVICE-1', 'decision=SERVICE-0')); history.back() })
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('SERVICE-1')
  await page.goForward()
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('SERVICE-0')
  await mkdir('../docs/reviews/ui/quality-round-34', { recursive: true })
  await page.getByLabel('搜索决策', { exact: true }).fill('SERVICE-1')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByLabel('决策详情', { exact: true }).screenshot({ path: `../docs/reviews/ui/quality-round-34/hidden-${width}.png`, animations: 'disabled' })
  }
})
