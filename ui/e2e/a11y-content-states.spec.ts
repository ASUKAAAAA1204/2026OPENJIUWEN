import { expect, test } from '@playwright/test'
import { inspectState } from './helpers/stateA11y'
import { authenticatedSession } from './helpers/authenticatedSession'
import { runContextFixture, lifecycleFixture } from '../tests/fixtures/lifecycle'
import { policyCatalogFixture, policySettingsFixture } from '../tests/fixtures/policies'
import { evaluationFixture } from '../tests/fixtures/evaluations'
import { createApprovalDemo } from '../src/components/workbench/approvals'
import { createDebtDemo } from '../src/components/workbench/debts'
import { createManagementDemo } from '../src/components/workbench/management'
import { createPreview } from '../src/components/workbench/model'
import { previewDecisions } from '../src/components/workbench/audit'

test('六板块服务成功内容、运行查询空集和设置分类四档可访问', async ({ page }) => {
  test.setTimeout(120000)
  await authenticatedSession(page)
  let emptyRuns = false
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: runContextFixture() }))
  await page.route('**/api/v27/runs?*', route => route.fulfill({ json: { source: 'ledger', items: emptyRuns ? [] : [lifecycleFixture().run], next_cursor: null } }))
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: createApprovalDemo(Date.now() + 600000) }))
  await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...createDebtDemo(), source: 'ledger' } }))
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  await page.route('**/api/v27/management', route => route.fulfill({ json: { ...createManagementDemo(), source: 'ledger' } }))
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(policyCatalogFixture()) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: policyCatalogFixture() }))
  const sections = [
    ['工作台', 'workspace', '.run-create-form'], ['运行记录', 'runs', '.service-run-history .history-card'],
    ['人工审批', 'approval', '.approval-actions'], ['证据与债务', 'debt', '.debt-list .history-card'],
    ['版本评估', 'evaluation', '.metrics-grid'], ['设置', 'settings', '.settings-panel .approval-facts'],
  ]
  for (const [section, query, ready] of sections) {
    await page.goto('about:blank')
    await page.goto(`/?${query}=api#${new URLSearchParams({ section })}`)
    await expect(page.locator(ready).first()).toBeVisible()
    await inspectState(page, `${query}-service-content`, '.workbench-content', 73)
    if (query === 'runs') {
      emptyRuns = true
      await page.getByRole('button', { name: '查询运行', exact: true }).click()
      await expect(page.locator('.service-run-history .history-card')).toHaveCount(0)
      await expect(page.locator('.service-run-history').getByRole('status')).toContainText('没有可见运行')
      await inspectState(page, 'runs-empty', '.workbench-content', 73)
    }
    if (query === 'settings') {
      for (const [category, name] of [['策略表', 'policy'], ['白名单与工具', 'whitelist'], ['框架接入', 'frameworks']]) {
        await page.locator('.settings-panel').getByRole('button', { name: category, exact: true }).click()
        await inspectState(page, `settings-${name}`, '.workbench-content', 73)
      }
    }
  }
})

for (const resource of ['events', 'claims']) test(`${resource}加载和空集合四档可访问`, async ({ page }) => {
  const run = createPreview('RUN-READ-STATES', '合成追溯状态', 'openJiuwen')
  const decision = previewDecisions(run)[1]
  await authenticatedSession(page)
  await page.route('**/api/v27/runs/RUN-READ-STATES/audit', route => route.fulfill({ json: { source: 'ledger', run_id: run.id, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [decision], chain: { status: 'unverified', reason: 'synthetic state fixture' } } }))
  let release = () => {}
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route(`**/${resource}`, async route => {
    await waiting
    await route.fulfill({ json: { source: 'ledger', run_id: run.id, ...(resource === 'events' ? { decision_id: decision.decision_id, events: [] } : { claims: [] }) } })
  })
  await page.goto(`/#run=${run.id}&framework=openJiuwen`)
  if (resource === 'events') await page.getByRole('button', { name: '事件时间线', exact: true }).click()
  else await page.getByRole('button', { name: '读取断言', exact: true }).click()
  const selector = resource === 'events' ? '.event-timeline' : '.run-claims'
  await expect(page.locator(selector).getByRole('status')).toContainText('正在读取')
  await inspectState(page, `${resource}-loading`, selector, 73)
  release()
  await expect(page.locator(selector)).toContainText(resource === 'events' ? '暂无可核验事件' : '空集合不表示声明一致率100%')
  await inspectState(page, `${resource}-empty-final`, selector, 73)
})

test('策略目录加载和空目录不提供可用切换动作', async ({ page }) => {
  await authenticatedSession(page)
  const catalog = { ...policyCatalogFixture(), active: null, versions: [] }
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog) }))
  let release = () => {}
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/policies', async route => { await waiting; await route.fulfill({ json: catalog }) })
  await page.goto('/?settings=api#section=设置&settings_category=策略表')
  const panel = page.locator('.policy-versions')
  await expect(panel.getByRole('status')).toContainText('正在读取')
  await inspectState(page, 'policy-directory-loading', '.settings-panel', 73)
  release()
  await expect(panel).toContainText('暂无可见的已发布版本')
  await expect(panel.locator('select, form')).toHaveCount(0)
  await inspectState(page, 'policy-directory-empty', '.settings-panel', 73)
})
