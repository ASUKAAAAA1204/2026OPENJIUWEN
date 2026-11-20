import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import { inspectState } from './helpers/stateA11y'
test('事件时间线展示真实来源边界和结果未知，权限失败清除旧记录', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-15', { recursive: true })
  const run = createPreview('RUN-DEMO-001', '事件测试', 'openJiuwen'), decision = previewDecisions(run)[0]
  await page.route('**/api/v27/runs/*/audit', route => route.fulfill({ json: { source: 'ledger', run_id: run.id, state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [decision], chain: { status: 'unverified', reason: '合成验收数据' } } }))
  let forbidden = false
  await page.route('**/decisions/*/events', route => route.fulfill(forbidden ? { status: 403, json: {} } : { json: { source: 'ledger', run_id: run.id, decision_id: decision.decision_id, events: ['GATE_DECIDED', 'EXEC_STARTED', 'UNKNOWN_OUTCOME'].map((type, index) => ({ event_id: `E${index}`, seq: index + 1, type, recorded_at: null, summary: ['策略允许，只授予执行权限', '执行入口已记录开始事件', '工具调用中断，实际副作用需核查'][index] })) } }))
  await page.goto('/?audit=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  await page.getByRole('button', { name: '事件时间线', exact: true }).click()
  await expect(page.locator('.event-timeline')).toContainText('执行结果未知')
  await inspectState(page, 'events-records-unknown', '.event-timeline', 71)
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.event-timeline').screenshot({ path: `../docs/reviews/ui/quality-round-15/events-${width}.png`, animations: 'disabled' })
  }
  forbidden = true
  await page.getByRole('button', { name: '刷新事件', exact: true }).click()
  await expect(page.locator('.event-timeline').getByRole('alert')).toContainText('无权')
  await expect(page.locator('.event-timeline li')).toHaveCount(0)
  await inspectState(page, 'events-forbidden', '.event-timeline', 71)
})
