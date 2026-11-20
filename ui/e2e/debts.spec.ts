import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { createDebtDemo } from '../src/components/workbench/debts'
import { authenticatedSession } from './helpers/authenticatedSession'
import { inspectState } from './helpers/stateA11y'
test.beforeEach(async ({ page }) => authenticatedSession(page))

test('父子债务单独列示，达到自身阈值展示复核状态，消解后保留历史读数', async ({ page }) => {
  const data = { ...createDebtDemo(), source: 'ledger' }
  data.items.push(data.items[2])
  await page.route('**/api/v27/debts', route => route.fulfill({ json: data }))
  await page.route('**/api/v27/debts/*/resolve', route => {
    const payload = route.request().postDataJSON()
    return route.fulfill({ json: { ...data.items[0], status: 'resolved', resolved: { seq: 4, method: payload.method, evidence_ref: payload.evidence_ref, regate_success: null } } })
  })
  await page.goto('/?debt=api#section=证据与债务')
  await page.locator('.debt-owners summary').click()
  const parent = page.locator('.debt-run-card').first()
  await expect(parent).toContainText('自身债务5子运行债务1含子运行汇总6自身复核阈值5')
  await expect(parent).toContainText('自身债务已达阈值')
  await expect(page.locator('.debt-run-card').nth(1)).toContainText('父运行：RUN-DEBT-DEMO')
  await expect(page.locator('.debt-workspace .summary-grid')).toContainText('未消解加权债务6')
  await mkdir('../docs/reviews/ui/quality-round-53', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-53/debt-runs-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.debt-workspace').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  await page.getByLabel('证据标识', { exact: true }).fill('test:evidence')
  await page.getByLabel('内容指纹 SHA-256').fill('b'.repeat(64))
  await page.getByLabel('我已核对消解方法与证据范围').check()
  await page.getByRole('button', { name: '提交消解核验' }).click()
  await expect(parent).toContainText('自身债务2子运行债务1含子运行汇总3自身复核阈值5')
  await expect(parent).toContainText('已登记的复核仍待处理')
  await expect(parent).toContainText('结束时自身读数：债务 5 · 未消解 2 条')
  await inspectState(page, 'debt-resolved-history', '.debt-workspace', 71)
})

test('未提供关系不假定父子或复核事实，循环关系拒绝汇总', async ({ page }) => {
  const data = { ...createDebtDemo(), source: 'ledger' }
  let corrupt = false
  await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...data, runs: corrupt ? [{ ...data.runs![0], parent_run_id: data.runs![1].run_id }, data.runs![1]] : null } }))
  await page.goto('/?debt=api#section=证据与债务')
  await page.locator('.debt-owners summary').click()
  await expect(page.locator('.debt-owners')).toContainText('无法判定父子关系或复核状态')
  await expect(page.locator('.debt-run-card')).toHaveCount(0)
  corrupt = true
  await page.getByRole('button', { name: '刷新债务', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('循环')
  await expect(page.locator('.debt-workspace .summary-grid')).toHaveCount(0)
})

test('债务提交等待期间保留当前页面，失败后恢复导航', async ({ page }) => {
  const data = { ...createDebtDemo(), source: 'ledger' }
  let release!: () => void, posted = false
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/debts', route => route.fulfill({ json: data }))
  await page.route('**/api/v27/debts/*/resolve', async route => { posted = true; await waiting; await route.fulfill({ status: 422, json: {} }) })
  await page.goto('/?debt=api#section=证据与债务')
  await page.getByLabel('证据标识', { exact: true }).fill('test:evidence')
  await page.getByLabel('内容指纹 SHA-256').fill('b'.repeat(64))
  await page.getByLabel('我已核对消解方法与证据范围').check()
  await page.getByRole('button', { name: '提交消解核验' }).click()
  await expect.poll(() => posted).toBe(true)
  await inspectState(page, 'debt-submitting', '.debt-workspace')
  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  await expect(page.locator('.debt-workspace')).toBeVisible()
  await expect(page.locator('.demo-notice')).toContainText('债务操作正在等待回执')
  await page.evaluate(() => { history.pushState(null, '', '#section=工作台'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.locator('.debt-workspace')).toBeVisible()
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.slice(1)).get('section')).toBe('证据与债务')
  await mkdir('../docs/reviews/ui/quality-round-38', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.locator('.demo-notice').screenshot({ path: `../docs/reviews/ui/quality-round-38/debt-${width}.png`, animations: 'disabled' })
  }
  release()
  await expect(page.getByRole('alert')).toContainText('债务仍保留')
  await inspectState(page, 'debt-rejected', '.debt-workspace')
  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  await expect(page.locator('.debt-workspace')).not.toBeVisible()
})

test('债务消解按标记核验表单，追加历史不删除原记录', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-5', { recursive: true })
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(3).click()
  await expect(page.locator('.debt-workspace .summary-grid')).toContainText('未消解加权债务6')
  await page.locator('.debt-list .history-card').nth(2).click()
  await page.getByLabel('演示证据标识').fill('demo:corrected-params')
  await page.getByLabel('内容指纹 SHA-256').fill('a'.repeat(64))
  await page.getByLabel('我已核对消解方法与证据范围').check()
  await page.getByRole('button', { name: '登记演示消解' }).click()
  await expect(page.getByRole('alert')).toContainText('重新过闸')
  await page.getByLabel('重新过闸决策标识').fill('demo:regate-decision')
  await expect(page.getByLabel('我已核对消解方法与证据范围')).not.toBeChecked()
  await page.getByLabel('我已核对消解方法与证据范围').check()
  await page.getByRole('button', { name: '登记演示消解' }).click()
  await expect(page.locator('.debt-history')).toContainText('原始登记')
  await expect(page.locator('.debt-history')).toContainText('demo:corrected-params')
  await expect(page.locator('.debt-workspace .summary-grid')).toContainText('未消解加权债务5')
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-5/resolved-1440.png', fullPage: true, animations: 'disabled' })
  await page.locator('.debt-workspace .approval-filters').getByRole('button', { name: '全部', exact: true }).click()
  await expect(page.locator('.debt-list .history-card')).toHaveCount(3)
  await page.locator('.debt-list .history-card').first().click()
  await page.locator('.debt-owners summary').click()
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-5/debt-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.debt-workspace').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
})

test('服务端核验失败保留债务，刷新后需重新核对', async ({ page }) => {
  const data = { ...createDebtDemo(), source: 'ledger' }
  let posts = 0
  await page.route('**/api/v27/debts', route => route.fulfill({ json: data }))
  await page.route('**/api/v27/debts/*/resolve', route => { posts++; return route.fulfill({ status: 422, json: {} }) })
  await page.goto('/?debt=api')
  await page.getByRole('navigation').getByRole('button').nth(3).click()
  await page.getByLabel('证据标识', { exact: true }).fill('test:evidence')
  await page.getByLabel('内容指纹 SHA-256').fill('b'.repeat(64))
  await page.getByLabel('我已核对消解方法与证据范围').check()
  await page.getByRole('button', { name: '提交消解核验' }).click()
  await expect(page.getByRole('alert')).toContainText('债务仍保留')
  await expect(page.getByRole('button', { name: '提交消解核验' })).toBeDisabled()
  await expect(page.locator('.debt-workspace .summary-grid')).toContainText('未消解加权债务6')
  expect(posts).toBe(1)
  await page.getByRole('button', { name: '刷新并重新核对' }).click()
  await expect(page.getByLabel('我已核对消解方法与证据范围')).not.toBeChecked()
})

test('成功响应不得改写原始债务，只有匹配的追加消解可更新界面', async ({ page }) => {
  const data = { ...createDebtDemo(), source: 'ledger' }
  const original = data.items[0]
  let corrupt = true
  await page.route('**/api/v27/debts', route => route.fulfill({ json: data }))
  await page.route('**/api/v27/debts/*/resolve', route => {
    const payload = route.request().postDataJSON()
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    return route.fulfill({ json: { ...original, cause: corrupt ? '非法改写原始原因' : original.cause, status: 'resolved', resolved: { seq: 3, method: payload.method, evidence_ref: payload.evidence_ref, regate_success: null } } })
  })
  await page.goto('/?debt=api')
  await page.getByRole('navigation').getByRole('button').nth(3).click()
  async function fill() {
    await page.getByLabel('证据标识', { exact: true }).fill('test:evidence')
    await page.getByLabel('内容指纹 SHA-256').fill('c'.repeat(64))
    await page.getByLabel('我已核对消解方法与证据范围').check()
    await page.getByRole('button', { name: '提交消解核验' }).click()
  }
  await fill()
  await expect(page.getByRole('alert')).toContainText('改变了原始条目')
  await expect(page.locator('.debt-workspace .summary-grid')).toContainText('未消解加权债务6')
  corrupt = false
  await page.getByRole('button', { name: '刷新并重新核对' }).click()
  await fill()
  await expect(page.locator('.debt-history')).toContainText('test:evidence')
  await expect(page.locator('.debt-workspace .summary-grid')).toContainText('未消解加权债务3')
})
