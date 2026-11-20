import { workbenchApi } from './helpers/workbenchApi'
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import { inspectState } from './helpers/stateA11y'

test.beforeEach(async ({ page }) => workbenchApi(page))

test('服务错误、权限、空记录、重试与证据数据渲染', async ({ page }) => {
  test.setTimeout(120000)
  await mkdir('../docs/reviews/ui/quality-round-3', { recursive: true })
  let status = 403
  let body: unknown = {}
  await page.route('**/api/v27/runs/*/audit', async route => {
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto('/?audit=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  await expect(page.getByRole('heading', { name: '访问受限' })).toBeVisible()
  await inspectState(page, 'detail-forbidden', '.run-detail', 68)
  await expect(page.locator('.decision-row')).toHaveCount(0)
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-3/forbidden-1440.png', fullPage: true, animations: 'disabled' })
  status = 401
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(page.getByRole('heading', { name: '需要重新登录' })).toBeVisible()
  await inspectState(page, 'detail-session-expired', '.run-detail', 68)
  status = 500
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(page.getByRole('heading', { name: '暂时无法加载' })).toBeVisible()
  await inspectState(page, 'detail-service-failure', '.run-detail', 68)
  status = 200
  const run = createPreview('RUN-DEMO-001', '测试', 'openJiuwen')
  body = { source: 'ledger', run_id: run.id, state: 'closed', segment: 2, parent_run_id: 'PARENT-1', child_run_ids: ['CHILD-1'], decisions: [], chain: { status: 'unverified', reason: '尚无签名检查点' } }
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(page.getByRole('heading', { name: '此运行暂无决策记录' })).toBeVisible()
  await inspectState(page, 'detail-empty', '.run-detail', 68)
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-3/empty-1440.png', fullPage: true, animations: 'disabled' })
  const decision = previewDecisions(run)[1]
  decision.status = 'ALLOWED'
  decision.gate_latency_ms = 4
  decision.policy_version = 'test-policy-v1'
  decision.rule_snapshot_ref = 'test-rule-ref'
  decision.evidence_ref = 'TEST-EVIDENCE-1'
  decision.evidence = { evidence_id: 'TEST-EVIDENCE-1', input_digest: '脱敏输入摘要', output_digest: '测试返回摘要', model_id: 'test-model', model_params: 'temperature=0', verdict: 'ALLOW', ts: '2026-11-19T12:00:00Z', content_hash: 'c'.repeat(64) }
  body = { ...body as object, decisions: [decision] }
  await page.getByRole('button', { name: '刷新记录' }).click()
  await expect(page.getByText('test-policy-v1 / 未加载')).toBeVisible()
  await inspectState(page, 'detail-decision', '.run-detail', 68)
  await page.getByRole('button', { name: '证据与账本', exact: true }).click()
  await expect(page.getByText('测试返回摘要')).toBeVisible()
  await expect(page.getByText('TEST-EVIDENCE-1', { exact: true })).toHaveCount(2)
  await inspectState(page, 'detail-evidence', '.run-detail', 68)
  await page.getByRole('button', { name: '事件时间线', exact: true }).click()
  await inspectState(page, 'detail-events', '.run-detail', 68)
  await page.getByRole('button', { name: '证据与账本', exact: true }).click()
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-3/evidence-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
})

test('取消旧请求后不会用旧运行内容覆盖预览', async ({ page }) => {
  test.setTimeout(60000)
  await page.route('**/api/v27/runs/*/audit', () => new Promise(() => {}))
  await page.goto('/?audit=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  await expect(page.getByRole('heading', { name: '正在读取运行账本' })).toBeVisible()
  await inspectState(page, 'detail-loading-reserved', '.run-detail', 70)
  await page.getByRole('button', { name: '← 返回运行记录' }).click()
  await expect(page.locator('.service-run-history')).toBeVisible()
  await expect(page.getByRole('heading', { name: '正在读取运行账本' })).toHaveCount(0)
})
