import type { Page } from '@playwright/test'
import { shellApi } from './shellApi'
import { createPreview } from '../../src/components/workbench/model'
import { previewDecisions } from '../../src/components/workbench/audit'
import { lifecycleFixture } from '../../tests/fixtures/lifecycle'
import { policyCatalogFixture, policySettingsFixture } from '../../tests/fixtures/policies'
import { evaluationFixture } from '../../tests/fixtures/evaluations'
import { createApprovalDemo } from '../../src/components/workbench/approvals'
import { createDebtDemo } from '../../src/components/workbench/debts'
import { createManagementDemo } from '../../src/components/workbench/management'

/** HTTP fixtures for product flows; production code always uses API adapters. */
export async function workbenchApi(page: Page) {
  await shellApi(page)
  const run = { ...createPreview('RUN-DEMO-001', '检查项目，整理文档修改建议', 'openJiuwen'), source: 'ledger', tools: [], state: 'closed', segment: 1, started_at: '2026-11-19T12:00:00Z', completed_at: '2026-11-19T12:01:00Z', parent_run_id: null, child_run_ids: [], decision_count: 2 }
  const decisions = previewDecisions(createPreview(run.id, run.title, 'openJiuwen')).map(item => ({ ...item, status: 'ALLOWED' }))
  await page.route('**/api/v27/runs?*', route => route.fulfill({ json: { source: 'ledger', items: [run], next_cursor: null } }))
  await page.route('**/api/v27/runs/*/audit', route => route.fulfill({ json: { source: 'ledger', run_id: route.request().url().split('/').slice(-2)[0], state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'Awaiting verification' } } }))
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: createApprovalDemo(Date.now() + 600000) }))
  await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...createDebtDemo(), source: 'ledger' } }))
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(policyCatalogFixture()) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: policyCatalogFixture() }))
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  const config = { ...createManagementDemo(), source: 'ledger' }
  await page.route('**/api/v27/management', route => route.fulfill({ json: config }))
  await page.route('**/api/v27/management/configure', route => {
    const body = route.request().postDataJSON()
    config.capabilities[body.capability as keyof typeof config.capabilities].enabled = body.enabled
    config.revision = crypto.randomUUID()
    config.last_change = { actor_id: 'TEST-ADMIN', at: new Date().toISOString(), reason: body.reason, audit_id: 'TEST-CONFIG' }
    return route.fulfill({ json: config })
  })
  await page.route('**/api/v27/management/assess-release', route => route.fulfill({ json: { target: route.request().postDataJSON().target, assessment_id: 'TEST-ASSESS', assessed_at: new Date().toISOString(), verdict: 'BLOCK', reasons: ['证据完整率未满足条件'], checks: [{ name: '证据完整率', passed: false, actual: '80%', required: '90%' }] } }))
  await page.route('**/api/v27/management/quote', route => route.fulfill({ json: { target: route.request().postDataJSON().target, quote_id: 'TEST-QUOTE', created_at: new Date().toISOString(), currency: 'CNY', total_minor: 20000, items: [{ item_id: 'Q1', decision_id: 'D1', action: '补齐证据', basis: '2 小时', amount_minor: 20000 }] } }))
  await page.route('**/api/v27/runs/create', route => route.fulfill({ json: { source: 'ledger', action: 'create', request_id: route.request().postDataJSON().request_id, audit_id: 'TEST-CREATE', created: true, snapshot: { ...lifecycleFixture(), task_id: route.request().postDataJSON().task_id, run: { ...lifecycleFixture().run, title: route.request().postDataJSON().title } } } }))
}
