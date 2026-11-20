import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import AxeBuilder from '@axe-core/playwright'
import { authenticatedSession } from './helpers/authenticatedSession'
import { inspectState } from './helpers/stateA11y'

test('规则快照缺失时拒绝一致结论，刷新后不可复现仍保留在分母', async ({ page }) => {
  await authenticatedSession(page)
  const runId = 'RUN-RULE-MISSING'
  const base = previewDecisions(createPreview(runId, '规则快照缺失测试', 'openJiuwen'))[1]
  const records = [{ ...base, decision_id: 'DEC-RULE-MISSING', status: 'ALLOWED', rule_snapshot_ref: null, evidence_ref: 'EVIDENCE-56', evidence: { evidence_id: 'EVIDENCE-56', input_digest: '合成输入', output_digest: '合成结果', model_id: 'fixture-model', model_params: '{}', verdict: 'ok', ts: '2026-11-19T12:00:00Z', content_hash: 'a'.repeat(64) } }]
  let falseMatch = true
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions: records, chain: { status: 'unverified', reason: '合成响应，缺少规则快照' } } }))
  await page.route(`**/api/v27/runs/${runId}/replay`, route => {
    const body = route.request().postDataJSON()
    return route.fulfill({ json: { source: 'ledger', run_id: runId, replay_id: 'REPLAY-56', completed_at: '2026-11-19T12:00:00Z', tool_execution: false, items: body.expected_decisions.map((item: Record<string, unknown>) => ({ ...item, result: falseMatch ? 'MATCH' : 'UNREPRODUCIBLE', reason: falseMatch ? '不可信的一致结论' : '缺少原始规则快照，无法重判' })) } })
  })
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  await page.getByRole('button', { name: '回放', exact: true }).click()
  const replay = page.locator('.service-replay')
  await replay.getByLabel('我已核对运行与证据快照，同意发起只读回放').check()
  await replay.getByRole('button', { name: '发起服务端回放' }).click()
  await expect(replay.getByRole('alert')).toContainText('不一致')
  await expect(replay.locator('.replay-result')).toHaveCount(0)
  await expect(replay.getByRole('button', { name: '发起服务端回放' })).toBeDisabled()
  await inspectState(page, 'replay-binding-error', '.service-replay', 67)
  await mkdir('../docs/reviews/ui/quality-round-56', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await replay.screenshot({ path: `../docs/reviews/ui/quality-round-56/replay-binding-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.service-replay').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  falseMatch = false
  await replay.getByRole('button', { name: '刷新运行账本', exact: true }).click()
  await page.getByRole('button', { name: '回放', exact: true }).click()
  await replay.getByLabel('我已核对运行与证据快照，同意发起只读回放').check()
  await replay.getByRole('button', { name: '发起服务端回放' }).click()
  await expect(replay.locator('.replay-result')).toContainText('复现一致：0 / 1（0.00%）')
  await expect(replay.locator('.replay-result')).toContainText('可评估覆盖：0 / 1')
  await expect(replay.locator('.replay-result')).toContainText('缺少原始规则快照')
  await inspectState(page, 'replay-unreproducible', '.service-replay', 67)
})
test('真实账本发起只读回放并按原决策分母展示缺证据', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-12', { recursive: true })
  const records = previewDecisions(createPreview('RUN-REAL', '账本运行', 'openJiuwen')).map((record, index) => ({ ...record, run_id: 'RUN-REAL', decision_id: `DEC-${index}`, status: 'ALLOWED', evidence_ref: null, evidence: null, rule_snapshot_ref: 'RULE-SNAPSHOT-1' }))
  await page.route('**/api/v27/runs/RUN-REAL/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-REAL', state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions: records, chain: { status: 'verified', reason: 'chain checked' } } }))
  await page.route('**/api/v27/session', route => route.fulfill({ json: { authenticated: true, csrf_token: 'fixture-csrf', identity: { user_id: 'AUDITOR', display_name: '审核员', role: '合规审计', scope: '全部只读' } } }))
  let posted = false
  let releaseReplay!: () => void
  const responseWait = new Promise<void>(resolve => { releaseReplay = resolve })
  await page.route('**/api/v27/runs/RUN-REAL/replay', async route => {
    const body = route.request().postDataJSON()
    expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf')
    expect(route.request().headers()['idempotency-key']).toBe(body.request_id)
    expect(body.execute_tools).toBe(false)
    posted = true
    await responseWait
    return route.fulfill({ json: { source: 'ledger', run_id: 'RUN-REAL', replay_id: 'REPLAY-1', completed_at: '2026-11-19T12:00:00Z', tool_execution: false, items: body.expected_decisions.map((item: Record<string, unknown>) => ({ ...item, result: 'UNREPRODUCIBLE', reason: '缺少结果证据，不可复现' })) } })
  })
  await page.route('**/api/v27/runs?*', route => route.fulfill({ json: { source: 'ledger', next_cursor: null, items: [{ id: 'RUN-REAL', title: '账本运行', framework: 'openJiuwen', tools: [], source: 'ledger', state: 'closed', segment: 1, started_at: '2026-11-19T11:00:00Z', completed_at: '2026-11-19T12:00:00Z', parent_run_id: null, child_run_ids: [], decision_count: 2 }] } }))
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history').getByLabel('运行来源').selectOption('api')
  await page.getByLabel('查询运行').fill('RUN-REAL')
  await page.locator('.service-run-history').getByRole('button', { name: '查询运行' }).click()
  await expect(page.locator('.service-run-history .history-card')).toContainText('RUN-REAL')
  await page.locator('.service-run-history .history-card').click()
  await expect(page.locator('.run-detail')).toContainText('RUN-REAL')
  await page.getByRole('button', { name: '回放', exact: true }).click()
  const replay = page.locator('.service-replay')
  await expect(replay.getByText('回放前提检查')).toHaveCount(0)
  await replay.getByLabel('我已核对运行与证据快照，同意发起只读回放').check()
  await replay.getByRole('button', { name: '发起服务端回放' }).click()
  await expect(page.getByRole('button', { name: '判定与参数', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '刷新账本', exact: true })).toBeDisabled()
  await expect(page.getByLabel('搜索决策', { exact: true })).toBeDisabled()
  await expect(page.locator('.decision-row').first()).toBeDisabled()
  await mkdir('../docs/reviews/ui/quality-round-31', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByLabel('决策详情', { exact: true }).screenshot({ path: '../docs/reviews/ui/quality-round-31/pending-' + width + '.png', animations: 'disabled' })
  }
  releaseReplay()
  await expect(replay).toContainText('复现一致：0 / 2（0.00%）')
  await expect(replay).toContainText('可评估覆盖：0 / 2')
  await expect(replay).toContainText('不可复现')
  expect(posted).toBe(true)
  const accessibility = await new AxeBuilder({ page }).include('.service-replay').analyze()
  expect(accessibility.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  for (const width of [1440,1024,390,320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await replay.screenshot({ path: `../docs/reviews/ui/quality-round-12/replay-${width}.png`, animations: 'disabled' })
  }
})
