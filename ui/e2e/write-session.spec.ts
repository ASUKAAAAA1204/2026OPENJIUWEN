import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { createApprovalDemo } from '../src/components/workbench/approvals'
import { createDebtDemo } from '../src/components/workbench/debts'
import { createManagementDemo } from '../src/components/workbench/management'
import { evaluationFixture } from '../tests/fixtures/evaluations'
import { testSession } from './helpers/authenticatedSession'

for (const section of ['approval', 'debt', 'management'] as const) {
  test(`${section} 登录失效时阻止写入，恢复会话后携带新令牌`, async ({ page }) => {
    await mkdir('../docs/reviews/ui/quality-round-16', { recursive: true })
    let authenticated = false, posts = 0
    await page.route('**/api/v27/session', route => route.fulfill({ json: authenticated ? { ...testSession, csrf_token: 'rotated-csrf' } : { authenticated: false, csrf_token: 'anonymous-csrf', identity: null } }))
    await page.route('**/api/v27/approvals', route => route.fulfill({ json: createApprovalDemo(Date.now() + 60000) }))
    await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...createDebtDemo(), source: 'ledger' } }))
    await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
    await page.route('**/api/v27/management', route => route.fulfill({ json: { ...createManagementDemo(), source: 'ledger' } }))
    const endpoint = section === 'approval' ? '**/api/v27/approvals/*/respond' : section === 'debt' ? '**/api/v27/debts/*/resolve' : '**/api/v27/management/configure'
    await page.route(endpoint, route => {
      posts++
      expect(route.request().headers()['x-csrf-token']).toBe('rotated-csrf')
      expect(route.request().headers()['idempotency-key']).toBe(route.request().postDataJSON().request_id)
      return route.fulfill({ status: 409, json: {} })
    })
    await page.goto(`/?${section === 'management' ? 'evaluation' : section}=api`)
    await page.getByRole('navigation').getByRole('button').nth(section === 'approval' ? 2 : section === 'debt' ? 3 : 4).click()
    const panel = page.locator(section === 'approval' ? '.approval-workspace' : section === 'debt' ? '.debt-workspace' : '.evaluation-management')
    async function fillAndSubmit() {
      if (section === 'approval') {
        await panel.getByLabel('我已核对目标、参数和操作范围').check()
        await panel.getByRole('button', { name: '批准请求', exact: true }).click()
      } else if (section === 'debt') {
        await panel.getByLabel('证据标识', { exact: true }).fill('test:evidence')
        await panel.getByLabel('内容指纹 SHA-256').fill('a'.repeat(64))
        await panel.getByLabel('我已核对消解方法与证据范围').check()
        await panel.getByRole('button', { name: '提交消解核验' }).click()
      } else {
        await panel.getByLabel('变更原因').fill('核验配置变更')
        await panel.getByLabel('我已核对版本、策略与变更影响。').check()
        await panel.getByRole('button', { name: '启用发布闸', exact: true }).click()
      }
    }
    await fillAndSubmit()
    await expect(panel.getByRole('alert')).toContainText('本次操作尚未提交')
    expect(posts).toBe(0)
    for (const width of section === 'approval' ? [1440, 1024, 390, 320] : [390]) {
      await page.setViewportSize({ width, height: 900 })
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await panel.screenshot({ path: `../docs/reviews/ui/quality-round-16/${section}-session-${width}.png`, animations: 'disabled' })
    }
    authenticated = true
    await panel.getByRole('button', { name: section === 'management' ? '刷新并确认状态' : '刷新并重新核对' }).click()
    await fillAndSubmit()
    await expect.poll(() => posts).toBe(1)
    await expect(panel.getByRole('alert')).toContainText('变化')
  })
}
