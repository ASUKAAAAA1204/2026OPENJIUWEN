import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { createApprovalDemo, type ApprovalAnswer } from '../src/components/workbench/approvals'
import { authenticatedSession } from './helpers/authenticatedSession'
import { inspectState } from './helpers/stateA11y'
test.beforeEach(async ({ page }) => authenticatedSession(page))

test('拒绝回执保留参数和责任主体，不提供再次批准入口', async ({ page }) => {
  const item = createApprovalDemo(Date.now() + 600000)[0]
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [item] }))
  await page.route('**/api/v27/approvals/*/respond', route => {
    const body = route.request().postDataJSON()
    expect(body.result).toBe('reject')
    return route.fulfill({ json: { ...item, status: 'rejected', revision: 'rejected-1', response: { approver_id: 'REJECTION-REVIEWER', approve_ts: new Date().toISOString(), approved_args_digest: item.args_digest, approve_result: 'reject', content_hash: 'synthetic-rejection', note: body.note } } })
  })
  await page.goto('/?approval=api#section=人工审批')
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByLabel('审批说明').fill('范围核对后拒绝此次发布')
  await page.getByRole('button', { name: '拒绝请求', exact: true }).click()
  await expect(page.locator('.approval-result')).toContainText('REJECTION-REVIEWER')
  await expect(page.locator('.approval-result')).toContainText('范围核对后拒绝此次发布')
  await expect(page.locator('.approval-actions')).toHaveCount(0)
  await inspectState(page, 'approval-rejected', '.approval-workspace', 73)
})

test('审批回执不得改写已核对的工具与授权范围', async ({ page }) => {
  const item = createApprovalDemo(Date.now())[0]
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [item] }))
  await page.route('**/api/v27/approvals/*/respond', route => route.fulfill({ json: { ...item, scope: '不同的未授权目录', status: 'approved', revision: 'changed', response: { approver_id: 'tester', approve_ts: new Date().toISOString(), approved_args_digest: item.args_digest, approve_result: 'approve', content_hash: 'test-hash', note: '合成回执' } } }))
  await page.goto('/?approval=api')
  await page.getByRole('navigation').getByRole('button').nth(2).click()
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByRole('button', { name: '批准请求', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('不能据此确认授权成功')
  await expect(page.locator('.approval-result')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '批准请求', exact: true })).toBeDisabled()
  await expect(page.locator('.approval-scope')).toHaveText(item.scope)
  await mkdir('../docs/reviews/ui/quality-round-50', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('alert').screenshot({ path: `../docs/reviews/ui/quality-round-50/receipt-conflict-${width}.png`, animations: 'disabled' })
  }
})

test('审批冲突需重新核对，提交绑定版本与实参且防重复', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-4', { recursive: true })
  const item = createApprovalDemo(Date.now() + 60000)[0]
  let conflict = true
  let requests = 0
  let lastAnswer: ApprovalAnswer | undefined
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [item] }))
  await page.route('**/api/v27/approvals/*/respond', async route => {
    requests++
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    lastAnswer = route.request().postDataJSON()
    if (conflict) return route.fulfill({ status: 409, json: {} })
    return route.fulfill({ json: { ...item, status: 'approved', revision: 'server-2', response: { approver_id: 'TEST-REVIEWER', approve_ts: new Date().toISOString(), approved_args_digest: item.args_digest, approve_result: 'approve', content_hash: 'test-receipt-hash', note: lastAnswer?.note } } })
  })
  await page.goto('/?approval=api')
  await page.getByRole('navigation').getByRole('button').nth(2).click()
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByLabel('审批说明').fill('已核查测试远端')
  await page.getByRole('button', { name: '批准请求', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('审批已变化')
  await expect(page.getByRole('button', { name: '批准请求', exact: true })).toBeDisabled()
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-4/conflict-1440.png', fullPage: true, animations: 'disabled' })
  conflict = false
  await page.getByRole('button', { name: '刷新并重新核对' }).click()
  await expect(page.getByLabel('我已核对目标、参数和操作范围')).not.toBeChecked()
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByLabel('审批说明').fill('重新核查通过')
  await page.getByRole('button', { name: '批准请求', exact: true }).click()
  await expect(page.locator('.approval-result')).toContainText('TEST-REVIEWER')
  expect(requests).toBe(2)
  expect(lastAnswer?.expected_revision).toBe(item.revision)
  expect(lastAnswer?.approved_args_digest).toBe(item.args_digest)
  expect(lastAnswer?.request_id).toBeTruthy()
  await expect(page.locator('.approval-actions')).toHaveCount(0)
  await inspectState(page, 'approval-approved', '.approval-workspace', 71)
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-4/approved-1440.png', fullPage: true, animations: 'disabled' })
})

test('审批多尺寸布局、键盘核对和到期保护', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-4', { recursive: true })
  await page.clock.install()
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(2).click()
  await page.getByRole('button', { name: '拒绝演示请求' }).click()
  await expect(page.getByRole('alert')).toContainText('请先确认')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-4/pending-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  await page.getByLabel('我已核对目标、参数和操作范围').focus()
  await page.keyboard.press('Space')
  await expect(page.getByLabel('我已核对目标、参数和操作范围')).toBeChecked()
  await page.clock.fastForward(31000)
  await page.getByRole('button', { name: '全部', exact: true }).click()
  await expect(page.getByRole('heading', { name: '审批窗口已结束' })).toBeVisible()
  await expect(page.locator('.approval-actions')).toHaveCount(0)
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-4/expired-320.png', fullPage: true, animations: 'disabled' })
  const result = await new AxeBuilder({ page }).include('.approval-workspace').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
})

test('提交中锁定操作，网络结果未知时强制重新读取', async ({ page }) => {
  const item = createApprovalDemo()[0]
  let release: () => void = () => {}
  const waiting = new Promise<void>(resolve => { release = resolve })
  let count = 0
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [item] }))
  await page.route('**/api/v27/approvals/*/respond', async route => {
    count++
    await waiting
    await route.abort('failed')
  })
  await page.goto('/?approval=api')
  await page.getByRole('navigation').getByRole('button').nth(2).click()
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByRole('button', { name: '批准请求', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在提交…' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '刷新队列' })).toBeDisabled()
  await expect(page.getByLabel('审批来源')).toBeDisabled()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button').first().click()
  await expect(page.getByRole('heading', { name: '人工审批', exact: true })).toBeVisible()
  await expect(page.locator('.demo-notice')).toContainText('审批操作正在等待回执')
  await mkdir('../docs/reviews/ui/quality-round-38', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.locator('.demo-notice').screenshot({ path: `../docs/reviews/ui/quality-round-38/approval-${width}.png`, animations: 'disabled' })
  }
  release()
  await expect(page.getByRole('alert')).toContainText('提交结果未知')
  await expect(page.getByRole('button', { name: '批准请求', exact: true })).toBeDisabled()
  expect(count).toBe(1)
  await page.getByRole('button', { name: '刷新并重新核对' }).click()
  await expect(page.getByLabel('我已核对目标、参数和操作范围')).not.toBeChecked()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button').first().click()
  await expect(page.locator('.task-workspace:visible').getByRole('heading', { level: 1 })).toBeVisible()
})
