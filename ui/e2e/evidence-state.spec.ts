import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'

test('证据正文缺失保留已有引用，不把缺失响应断言成事件未发生', async ({ page }) => {
  const runId = 'RUN-EVIDENCE-STATE'
  const base = previewDecisions(createPreview(runId, 'evidence', 'openJiuwen'))[1]
  const decisions = [
    { ...base, decision_id: 'DEC-REFS', tool_name: '有引用无正文', evidence_ref: 'EVIDENCE-REF-KEPT', rule_snapshot_ref: 'RULE-REF-KEPT', evidence: null },
    { ...base, decision_id: 'DEC-NO-REFS', tool_name: '未返回引用', evidence_ref: null, rule_snapshot_ref: null, evidence: null },
  ]
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'invalid', reason: '检查点与前序哈希不匹配（测试数据）' } } }))
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  await page.getByRole('button', { name: '证据与账本', exact: true }).click()
  const region = page.getByRole('region', { name: '证据与账本', exact: true })
  await expect(region).toContainText('尚无可核验证据')
  await expect(region).toContainText('EVIDENCE-REF-KEPT')
  await expect(region).toContainText('RULE-REF-KEPT')
  await expect(region).toContainText('此响应未提供，待核对')
  await expect(region).toContainText('服务报告校验失败')
  await expect(region).toContainText('检查点与前序哈希不匹配')
  await expect(region.locator('dd')).not.toContainText(['未产生'])
  await mkdir('../docs/reviews/ui/quality-round-29', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByLabel('决策详情', { exact: true }).screenshot({ path: `../docs/reviews/ui/quality-round-29/evidence-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.audit-detail').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  await page.locator('.decision-row').filter({ hasText: 'DEC-NO-REFS' }).click()
  await expect(region).toContainText('未返回引用')
  await expect(region).not.toContainText('EVIDENCE-REF-KEPT')
  await expect(region).not.toContainText('RULE-REF-KEPT')
})
