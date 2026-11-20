import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import { inspectState } from './helpers/stateA11y'

test('断言独立展示关联与缺决策风险，失败清空旧记录且可重读空集合', async ({ page }) => {
  const claim = { claim_id: 'CLAIM-1', text: '模块已经通过核对', locator: 'report.md:12', source_ref: 'artifact:report-v1', decision_id: 'DECISION-1', evidence_ref: 'EVIDENCE-1', location_status: 'LOCATED', verification: 'MATCH', missing_decision_mark: null }
  const missing = { ...claim, claim_id: 'CLAIM-2', text: '发布结果尚待核查', locator: null, decision_id: null, evidence_ref: null, location_status: 'UNLOCATED', verification: 'UNKNOWN', missing_decision_mark: { mark_type: 'NO_EVIDENCE', level: 'high', review_ref: 'REVIEW-1' } }
  let status = 200, claims = [claim, missing], reads = 0
  await page.route('**/api/v27/runs/RUN-CLAIMS/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-CLAIMS', state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: 'fixture' } } }))
  await page.route('**/api/v27/runs/RUN-CLAIMS/claims', route => { reads++; return route.fulfill({ status, json: { source: 'ledger', run_id: 'RUN-CLAIMS', claims } }) })
  await page.goto('/#run=RUN-CLAIMS&framework=openJiuwen')
  const panel = page.getByLabel('终稿断言追溯', { exact: true })
  await expect(panel).toContainText('尚未读取断言')
  expect(reads).toBe(0)
  await panel.getByRole('button', { name: '读取断言', exact: true }).click()
  await expect(panel).toContainText('2 条断言 · 缺决策 1 条')
  await expect(panel).toContainText('MISSING_DECISION · 风险未知')
  await expect(panel).toContainText('REVIEW-1')
  await expect(panel).toContainText('report.md:12')
  await inspectState(page, 'claims-records-missing', '.run-claims', 71)
  await mkdir('../docs/reviews/ui/quality-round-27', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-27/claims-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.run-claims').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  status = 403
  await panel.getByRole('button', { name: '刷新断言' }).click()
  await expect(panel.getByRole('alert')).toContainText('无权')
  await expect(panel.locator('.claim-records')).toHaveCount(0)
  await inspectState(page, 'claims-forbidden', '.run-claims', 71)
  status = 200; claims = []
  await panel.getByRole('button', { name: '读取断言', exact: true }).click()
  await expect(panel).toContainText('空集合不表示声明一致率100%')
  await inspectState(page, 'claims-empty', '.run-claims', 71)
})

test('断言定位清除冲突筛选并把焦点交给指定决策，孤立引用不跳转', async ({ page }) => {
  const runId = 'RUN-CLAIM-LINK'
  const base = previewDecisions(createPreview(runId, 'link', 'openJiuwen'))[1]
  const decisions = [{ ...base, decision_id: 'DEC-FIRST', tool_name: '第一项' }, { ...base, decision_id: 'DEC-TARGET', tool_name: '断言关联工具' }]
  const claim = { claim_id: 'CLAIM-LINK', text: '关联结果待核查', locator: null, source_ref: 'report:1', decision_id: 'DEC-TARGET', evidence_ref: null, location_status: 'UNLOCATED', verification: 'UNKNOWN', missing_decision_mark: null }
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'synthetic fixture' } } }))
  await page.route(`**/api/v27/runs/${runId}/claims`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, claims: [claim, { ...claim, claim_id: 'CLAIM-ORPHAN', decision_id: 'DEC-NOT-IN-SNAPSHOT' }] } }))
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  const panel = page.getByLabel('终稿断言追溯', { exact: true })
  await panel.getByRole('button', { name: '读取断言', exact: true }).click()
  await expect(panel).toContainText('当前账本快照未包含此关联决策')
  await expect(panel.getByRole('button', { name: /定位 .* 的关联决策/ })).toHaveCount(1)
  await page.getByLabel('搜索决策', { exact: true }).fill('no-match')
  await page.getByLabel('Gate 判定', { exact: true }).selectOption('DENY')
  const locate = panel.getByRole('button', { name: '定位 CLAIM-LINK 的关联决策' })
  await locate.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '断言关联工具', exact: true })).toBeFocused()
  await expect(page.getByLabel('搜索决策', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('Gate 判定', { exact: true })).toHaveValue('全部判定')
  await expect(page.getByRole('region', { name: '判定与参数', exact: true })).toContainText('DEC-TARGET')
  await expect(page.locator('.decision-row[aria-pressed="true"]')).toContainText('DEC-TARGET')
  await mkdir('../docs/reviews/ui/quality-round-28', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-28/claim-links-${width}.png`, animations: 'disabled' })
  }
})
