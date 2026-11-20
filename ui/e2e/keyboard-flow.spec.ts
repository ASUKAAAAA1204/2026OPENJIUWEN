import { expect, test, type Locator, type Page } from '@playwright/test'
import { authenticatedSession } from './helpers/authenticatedSession'
import { runContextFixture, lifecycleFixture } from '../tests/fixtures/lifecycle'
import { createApprovalDemo } from '../src/components/workbench/approvals'
import { createDebtDemo } from '../src/components/workbench/debts'
import { policyCatalogFixture, policySettingsFixture, policyReceiptFixture } from '../tests/fixtures/policies'
import { mkdir, writeFile } from 'node:fs/promises'

async function tabTo(page: Page, target: Locator) {
  await expect(target).toBeVisible()
  await expect(target).toBeEnabled()
  for (let steps = 0; steps < 150; steps++) {
    if (await target.evaluate(node => document.activeElement === node)) {
      await expect(target).toBeFocused()
      await expect(target).toHaveCSS('outline-width', '2px')
      return
    }
    await page.keyboard.press('Tab')
  }
  throw new Error(`Tab顺序无法到达目标：${await target.getAttribute('aria-label') ?? await target.textContent()}`)
}
async function activate(page: Page, target: Locator, key = 'Enter') { await tabTo(page, target); await page.keyboard.press(key) }
async function type(page: Page, target: Locator, value: string) { await tabTo(page, target); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.insertText(value) }

for (const width of [1440, 320]) test(`${width}px只用键盘完成创建、拒绝审批、消解及策略切换`, async ({ page }) => {
  test.setTimeout(120000)
  await page.setViewportSize({ width, height: 900 })
  await authenticatedSession(page)
  const approval = createApprovalDemo(Date.now() + 600000)[0], catalog = policyCatalogFixture()
  const posts: string[] = []
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: runContextFixture() }))
  await page.route('**/api/v27/runs/create', route => { posts.push('create'); return route.fulfill({ json: { source: 'ledger', action: 'create', request_id: route.request().postDataJSON().request_id, audit_id: 'KEYBOARD-CREATE', created: true, snapshot: lifecycleFixture() } }) })
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [approval] }))
  await page.route('**/api/v27/approvals/*/respond', route => {
    posts.push('reject')
    const body = route.request().postDataJSON()
    expect(body.result).toBe('reject')
    return route.fulfill({ json: { ...approval, status: 'rejected', revision: 'keyboard-rejected', response: { approver_id: 'KEYBOARD-REVIEWER', approve_ts: new Date().toISOString(), approved_args_digest: approval.args_digest, approve_result: 'reject', content_hash: 'synthetic-rejection', note: body.note } } })
  })
  await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...createDebtDemo(), source: 'ledger' } }))
  await page.route('**/api/v27/debts/*/resolve', route => { posts.push('resolve'); const body = route.request().postDataJSON(); return route.fulfill({ json: { ...createDebtDemo().items[0], status: 'resolved', resolved: { seq: 4, method: body.method, evidence_ref: body.evidence_ref, regate_success: null } } }) })
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: catalog }))
  await page.route('**/api/v27/policies/activate', route => { posts.push('policy'); const body = route.request().postDataJSON(); return route.fulfill({ json: policyReceiptFixture(catalog, body.request_id, body.reason) }) })
  await page.goto('/?workspace=api&approval=api&debt=api&settings=api')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: '跳转到工作区' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#workspace-content')).toBeFocused()
  await type(page, page.getByLabel('业务任务标识', { exact: true }), 'BUSINESS-42')
  await type(page, page.getByLabel('任务描述', { exact: true }), '核查项目文件并生成修改建议')
  await activate(page, page.getByLabel('授权工作目录', { exact: true }), 'End')
  await expect(page.getByLabel('授权工作目录', { exact: true })).toHaveValue('workspace-1')
  await activate(page, page.getByLabel('Skill 与版本', { exact: true }), 'End')
  await activate(page, page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本'), 'Space')
  await activate(page, page.getByRole('button', { name: '创建账本运行', exact: true }))
  await expect(page.locator('.run-create-panel').getByRole('status')).toContainText('运行已登记')
  await activate(page, page.getByRole('navigation').getByRole('button', { name: /人工审批/ }))
  await activate(page, page.getByLabel('我已核对目标、参数和操作范围'), 'Space')
  await type(page, page.getByLabel('审批说明'), '键盘核对后拒绝此次发布')
  await activate(page, page.getByRole('button', { name: '拒绝请求', exact: true }))
  await expect(page.locator('.approval-result')).toContainText('KEYBOARD-REVIEWER')
  await activate(page, page.getByRole('navigation').getByRole('button', { name: /证据与债务/ }))
  await type(page, page.getByLabel('证据标识', { exact: true }), 'keyboard:evidence')
  await type(page, page.getByLabel('内容指纹 SHA-256'), 'b'.repeat(64))
  await activate(page, page.getByLabel('我已核对消解方法与证据范围'), 'Space')
  await activate(page, page.getByRole('button', { name: '提交消解核验', exact: true }))
  await expect(page.locator('.debt-workspace')).toContainText('服务端已返回消解记录')
  await activate(page, page.getByRole('button', { name: '⚙ 设置', exact: true }))
  await activate(page, page.locator('.settings-panel').getByRole('button', { name: '策略表', exact: true }))
  await activate(page, page.getByLabel('候选策略版本', { exact: true }), 'End')
  await type(page, page.getByLabel('策略切换原因', { exact: true }), '键盘检查完整差异后切换')
  await activate(page, page.getByLabel('我已核对完整配置差异、启用范围与历史快照边界'), 'Space')
  await activate(page, page.getByRole('button', { name: '确认切换策略版本', exact: true }))
  await expect(page.locator('.settings-panel').getByRole('status').filter({ hasText: '已确认切换至' })).toContainText('policy-2.7.2')
  expect(posts).toEqual(['create', 'reject', 'resolve', 'policy'])
  await mkdir('../docs/reviews/ui/quality-round-74', { recursive: true })
  await writeFile(`../docs/reviews/ui/quality-round-74/keyboard-${width}.json`, JSON.stringify({ width, input: 'Tab/Enter/Space/End/text only; no click/focus/fill/selectOption', operations: posts, note: 'synthetic responses; not a spoken screen-reader test' }, null, 2))
})
