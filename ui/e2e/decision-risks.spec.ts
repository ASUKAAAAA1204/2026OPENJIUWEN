import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'

test('未覆盖及未知结果明确告警，定位清理冲突筛选且保持键盘焦点', async ({ page }) => {
  const base = previewDecisions(createPreview('RUN-RISK', 'risk', 'openJiuwen'))[1]
  const decisions = [
    { ...base, decision_id: 'UNHOOKED-1', class: 'UNCLASSIFIED', gate: 'NONE', status: 'UNHOOKED' },
    { ...base, decision_id: 'UNKNOWN-1', status: 'UNKNOWN_OUTCOME' },
    { ...base, decision_id: 'UNCLASSIFIED-ONLY', class: 'UNCLASSIFIED', status: 'ALLOWED' },
  ]
  let denied = false
  await page.route('**/api/v27/runs/RUN-RISK/audit', route => denied ? route.fulfill({ status: 403, json: {} }) : route.fulfill({ json: { source: 'ledger', run_id: 'RUN-RISK', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'fixture only' } } }))
  await page.goto('/#run=RUN-RISK&framework=openJiuwen')
  const panel = page.getByLabel('需要核对的动作', { exact: true })
  await expect(panel).toContainText('1 条 UNHOOKED')
  await expect(panel).toContainText('1 条 UNKNOWN_OUTCOME')
  await page.getByLabel('搜索决策', { exact: true }).fill('no-match')
  await page.getByLabel('Gate 判定', { exact: true }).selectOption('DENY')
  await panel.getByRole('button', { name: '定位未覆盖动作' }).click()
  await expect(page.getByLabel('异常动作', { exact: true })).toBeFocused()
  await expect(page.locator('.decision-row')).toHaveCount(1)
  await expect(page.locator('.decision-row')).toContainText('UNHOOKED-1')
  await panel.getByRole('button', { name: '定位结果未知动作' }).click()
  await expect(page.locator('.decision-row')).toContainText('UNKNOWN-1')
  await expect(panel).toContainText('结果未知不等于失败')
  await mkdir('../docs/reviews/ui/quality-round-25', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-25/risks-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.decision-risk-panel').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  denied = true
  await page.reload()
  await expect(page.getByRole('heading', { name: '访问受限' })).toBeVisible()
  await expect(panel).toHaveCount(0)
})

test('预览和仅未分类记录不伪造真实未覆盖动作告警', async ({ page }) => {
  const base = previewDecisions(createPreview('RUN-UNCLASSIFIED', 'test', 'openJiuwen'))[1]
  await page.route('**/api/v27/runs/RUN-UNCLASSIFIED/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-UNCLASSIFIED', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [{ ...base, class: 'UNCLASSIFIED', status: 'ALLOWED' }], chain: { status: 'unverified', reason: 'fixture only' } } }))
  await page.goto('/#run=RUN-UNCLASSIFIED&framework=openJiuwen')
  await expect(page.locator('.decision-row')).toHaveCount(1)
  await expect(page.getByLabel('需要核对的动作', { exact: true })).toHaveCount(0)
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  await expect(page.getByLabel('需要核对的动作', { exact: true })).toHaveCount(0)
})
