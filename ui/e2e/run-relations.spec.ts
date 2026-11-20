import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
test('父子运行可导航、深链接可回退且每次重验权限', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-14', { recursive: true })
  const parent = { source: 'ledger', run_id: 'RUN-PARENT', state: 'closed', segment: 1, parent_run_id: null, child_run_ids: ['RUN-CHILD'], decisions: [], chain: { status: 'verified', reason: 'parent hash checked' } }
  const child = { ...parent, run_id: 'RUN-CHILD', parent_run_id: 'RUN-PARENT', child_run_ids: [], decisions: previewDecisions(createPreview('RUN-CHILD', 'Child secret title', 'LangGraph')).map((item, index) => ({ ...item, run_id: 'RUN-CHILD', decision_id: `CHILD-DEC-${index}` })) }
  let deniedChild = false, childReads = 0
  await page.route('**/api/v27/runs/RUN-PARENT/audit', route => route.fulfill({ json: parent }))
  await page.route('**/api/v27/runs/RUN-CHILD/audit', route => { childReads++; return route.fulfill(deniedChild ? { status: 403, json: {} } : { json: child }) })
  await page.route('**/api/v27/runs?*', route => route.fulfill({ json: { source: 'ledger', next_cursor: null, items: [{ source: 'ledger', id: 'RUN-PARENT', title: 'Parent run', framework: 'LangGraph', tools: [], state: 'closed', segment: 1, started_at: '2026-11-19T11:00:00Z', completed_at: '2026-11-19T12:00:00Z', parent_run_id: null, child_run_ids: ['RUN-CHILD'], decision_count: 0 }] } }))
  await page.goto('/?runs=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()

  await page.locator('.service-run-history').getByLabel('查询运行').fill('RUN-PARENT')
  await page.locator('.service-run-history').getByRole('button', { name: '查询运行' }).click()
  await page.locator('.service-run-history .history-card').click()
  await expect(page).toHaveURL(/#run=RUN-PARENT/)
  await page.getByRole('button', { name: '子运行 RUN-CHILD' }).click()
  await expect(page).toHaveURL(/#run=RUN-CHILD/)
  await expect(page.locator('.run-detail')).toContainText('父运行')
  expect(new URL(page.url()).hash).not.toContain('Child secret title')
  await page.goBack()
  await expect(page.locator('.run-detail')).toContainText('RUN-PARENT')
  expect(childReads).toBe(1)
  await page.reload()
  await expect(page.locator('.run-relations')).toContainText('RUN-CHILD')
  const result = await new AxeBuilder({ page }).include('.run-relations').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  for (const width of [1440,1024,390,320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.run-relations').screenshot({ path: `../docs/reviews/ui/quality-round-14/relations-${width}.png`, animations: 'disabled' })
  }
  deniedChild = true
  await page.getByRole('button', { name: '子运行 RUN-CHILD' }).click()
  await expect(page.locator('.run-detail').getByRole('alert')).toContainText('无权')
  expect(childReads).toBe(2)

})
