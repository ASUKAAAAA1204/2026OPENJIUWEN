import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { createApprovalDemo } from '../src/components/workbench/approvals'
import { createDebtDemo } from '../src/components/workbench/debts'
import { authenticatedSession } from './helpers/authenticatedSession'

test('审批和债务刷新恢复原条目，表单不恢复，失效定位不替换为其他条目', async ({ page }) => {
  await authenticatedSession(page)
  const approvals = createApprovalDemo(Date.now() + 60000), debts = createDebtDemo()
  let writes = 0
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: approvals }))
  await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...debts, source: 'ledger' } }))
  page.on('request', request => { if (request.method() === 'POST') writes++ })
  await page.goto(`/#${new URLSearchParams({ section: '人工审批', queue_source: 'api', queue_filter: '全部', queue_item: approvals[0]!.approval_id })}`)
  await expect(page.locator('.approval-card')).toContainText(approvals[0]!.approval_id)
  await page.getByLabel('审批说明').fill('不应持久保存的说明')
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.reload()
  await expect(page.getByLabel('审批说明')).toHaveValue('')
  await expect(page.getByLabel('我已核对目标、参数和操作范围')).not.toBeChecked()
  const id = debts.items[1]!.debt_id
  await page.goto(`/#${new URLSearchParams({ section: '证据与债务', queue_source: 'api', queue_filter: '全部', queue_item: id })}`)
  await expect(page.locator('.debt-detail')).toContainText(id)
  await page.getByLabel('证据标识', { exact: true }).fill('sensitive-evidence')
  await page.reload()
  await expect(page.locator('.debt-detail')).toContainText(id)
  await expect(page.getByLabel('证据标识', { exact: true })).toHaveValue('')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.getByRole('navigation').getByRole('button').nth(3).click()
  await expect(page.locator('.debt-detail')).toContainText(id)
  await page.reload()
  await expect(page.locator('.debt-detail')).toContainText(id)
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.goBack()
  await expect(page.locator('.debt-detail')).toContainText(id)
  await page.goto('/#section=证据与债务&queue_source=api&queue_filter=全部&queue_item=missing')
  await expect(page.getByRole('heading', { name: '所选债务当前不可见' })).toBeVisible()
  await expect(page.locator('.debt-detail')).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-59', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-59/debt-location-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  await page.getByRole('button', { name: '清除债务定位' }).click()
  await expect(page.locator('.debt-detail')).toBeVisible()
  await expect(page.getByRole('combobox', { name: '债务来源', exact: true })).toHaveCount(0)
  expect(writes).toBe(0)
})
