import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'

test('责任链按快照展示，委派失败不伪造子运行绑定，四档截图', async ({ page }) => {
  const id = 'RUN-DELEGATE-LONG-IDENTIFIER-1234567890'
  const base = previewDecisions(createPreview(id, 'test', 'openJiuwen'))[1]
  const decisions = [{ ...base, class: 'delegate', status: 'EXEC_FAILED', gate_reason: '子任务启动失败，原始结果保留' }]
  await page.route(`**/api/v27/runs/${id}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: id, state: 'degraded', segment: 3, parent_run_id: 'RUN-ROOT', child_run_ids: ['RUN-CHILD-A', 'RUN-CHILD-B'], decisions, chain: { status: 'unverified', reason: '测试快照，未提供签名检查点' } } }))
  await page.goto(`/#run=${id}&framework=openJiuwen`)
  const panel = page.getByLabel('委派关联', { exact: true })
  await expect(panel).toContainText('第 3 段 · 降级运行')
  await expect(panel).toContainText('2 个直接子运行')
  await panel.getByText('当前运行的委派决策 · 1 条').click()
  await expect(panel).toContainText('结果状态 EXEC_FAILED')
  await expect(panel).toContainText('不能按列表顺序猜测对应关系')
  await expect(panel).toContainText('不推断全图或委派深度')
  await mkdir('../docs/reviews/ui/quality-round-22', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-22/relations-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.run-relations').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  await panel.getByRole('button', { name: '子运行 RUN-CHILD-B' }).focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/#run=RUN-CHILD-B/)
})
