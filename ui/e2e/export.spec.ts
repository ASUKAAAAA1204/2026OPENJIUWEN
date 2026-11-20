import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'
import AxeBuilder from '@axe-core/playwright'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import { inspectState } from './helpers/stateA11y'
test('授权导出核对审计与文件指纹，坏文件不下载', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-13', { recursive: true })
  const run = createPreview('RUN-DEMO-001', '合成导出测试', 'openJiuwen')
  const content = JSON.stringify({ run_id: run.id, event: 'TEST_FIXTURE_ONLY' }) + '\n'
  let badHash = true, downloads = 0
  page.on('download', () => { downloads++ })
  await page.route('**/api/v27/runs/*/audit', route => route.fulfill({ json: { source: 'ledger', run_id: run.id, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions: previewDecisions(run), chain: { status: 'unverified', reason: 'Synthetic fixture' } } }))
  await page.route('**/api/v27/session', route => route.fulfill({ json: { authenticated: true, csrf_token: 'test-csrf', identity: { user_id: 'AUDITOR', display_name: '审计', role: '合规审计', scope: '全部只读' } } }))
  await page.route('**/api/v27/runs/*/export', route => {
    expect(route.request().headers()['x-csrf-token']).toBe('test-csrf')
    expect(route.request().headers()['idempotency-key']).toBe(route.request().postDataJSON().request_id)
    return route.fulfill({ contentType: 'application/x-ndjson', headers: { 'X-Run-Id': run.id, 'X-Export-Audit-Id': 'EXPORT-TEST-1', 'X-Content-SHA256': badHash ? '0'.repeat(64) : createHash('sha256').update(content).digest('hex') }, body: content })
  })
  await page.goto('/?audit=api')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-card').first().click()
  await page.getByText('授权导出 JSONL', { exact: true }).click()
  const panel = page.locator('.ledger-export')
  await inspectState(page, 'export-ready', '.ledger-export', 67)
  await panel.getByLabel('我已确认此运行的导出范围和文件保存位置').check()
  await panel.getByRole('button', { name: '申请账本导出' }).click()
  await expect(panel.getByRole('alert')).toContainText('指纹不匹配')
  expect(downloads).toBe(0)
  await inspectState(page, 'export-invalid-file', '.ledger-export', 67)
  badHash = false
  await panel.getByLabel('我已确认此运行的导出范围和文件保存位置').check()
  const pendingDownload = page.waitForEvent('download')
  await panel.getByRole('button', { name: '申请账本导出' }).click()
  const downloaded = await pendingDownload
  expect(downloaded.suggestedFilename()).toBe('RUN-DEMO-001-ledger.jsonl')
  expect(await readFile((await downloaded.path())!, 'utf8')).toBe(content)
  await expect(panel.getByRole('status')).toContainText('EXPORT-TEST-1')
  await inspectState(page, 'export-completed', '.ledger-export', 67)
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-13/export-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.ledger-export').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
})
