import { workbenchApi } from './helpers/workbenchApi'
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { decisionClasses } from '../src/components/workbench/decisionClasses'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'

test.beforeEach(async ({ page }) => workbenchApi(page))

test('11类及未知记录分列，组合筛选不改变观察分母，四档布局验收', async ({ page }) => {
  const base = previewDecisions(createPreview('RUN-CLASSES', 'test', 'openJiuwen'))[1]
  const decisions = [...decisionClasses.map(([key]) => key), 'FILE_WRITE', 'UNCLASSIFIED'].map((value, i) => ({ ...base, decision_id: `DEC-${i}`, class: value, status: 'ALLOWED' }))
  await page.route('**/api/v27/runs/RUN-CLASSES/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-CLASSES', state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'test only' } } }))
  await page.goto('/#run=RUN-CLASSES&framework=openJiuwen')
  const panel = page.getByLabel('决策类别分布', { exact: true })
  await expect(panel).toContainText('本次出现 11 / 11')
  await expect(panel.locator('.decision-class-card')).toHaveCount(12)
  await expect(panel.locator('.decision-class-card').first()).not.toBeVisible()
  const list = page.getByRole('region', { name: '决策列表，可滚动查看全部记录' })
  await list.focus()
  await page.keyboard.press('End')
  await expect.poll(() => list.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  await page.locator('.decision-row').last().click()
  await expect(page.locator('.audit-detail')).toContainText('DEC-12')
  await page.locator('.decision-row').first().click()
  await mkdir('../docs/reviews/ui/quality-round-69', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const accessibility = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
    expect(accessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
    await page.locator('.run-detail').screenshot({ path: `../docs/reviews/ui/quality-round-69/detail-priority-${width}.png`, animations: 'disabled' })
  }
  await panel.locator('summary').focus()
  await page.keyboard.press('Enter')
  await panel.getByRole('button', { name: /文件写入/ }).click()
  await expect(page.locator('.decision-row')).toHaveCount(2)
  await page.getByLabel('Gate 判定', { exact: true }).selectOption('DENY')
  await expect(page.getByRole('heading', { name: '没有匹配的决策' })).toBeVisible()
  await expect(panel).toContainText('本次出现 11 / 11')
  await page.getByRole('button', { name: '清除筛选', exact: true }).click()
  await expect(page.locator('.decision-row')).toHaveCount(13)
  await panel.getByRole('button', { name: /UNCLASSIFIED/ }).click()
  await expect(page.locator('.decision-row')).toHaveCount(1)
  await expect(page.locator('.decision-row')).toContainText('UNCLASSIFIED')
  await mkdir('../docs/reviews/ui/quality-round-21', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-21/classes-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.decision-classes').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
})

test('空运行保留零观察，预览和白名单外类别不会冒充接入覆盖', async ({ page }) => {
  await page.route('**/api/v27/runs/RUN-EMPTY/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-EMPTY', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: 'test only' } } }))
  await page.goto('/#run=RUN-EMPTY&framework=LangGraph')
  await expect(page.getByLabel('决策类别分布', { exact: true })).toContainText('本次出现 0 / 11')
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.locator('.run-history .history-list .history-card').first().click()
  const panel = page.getByLabel('决策类别分布', { exact: true })
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('本次出现 1 / 11')
  await panel.locator('summary').click()
  await expect(panel).toContainText('READ_ONLY_PREVIEW')
  await expect(panel.getByRole('button', { name: /READ_ONLY_PREVIEW/ })).toBeVisible()
})
