import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { createManagementDemo } from '../src/components/workbench/management'
import { evaluationFixture } from '../tests/fixtures/evaluations'
import { authenticatedSession } from './helpers/authenticatedSession'
import { versionKey } from '../src/components/workbench/evaluations'
import { inspectState } from './helpers/stateA11y'

test('评估版本与单次范围刷新及后退恢复，失效范围不冒充聚合读数', async ({ page }) => {
  await authenticatedSession(page)
  const snapshot = evaluationFixture(), config = { ...createManagementDemo(), source: 'ledger' }
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: snapshot }))
  await page.route('**/api/v27/management', route => route.fulfill({ json: config }))
  await page.goto('/?evaluation=api#section=版本评估')
  const target = snapshot.versions[1]!, key = versionKey(target), run = target.sample_runs[0]!.run_id
  await page.getByLabel('Skill 与版本').selectOption(key)
  await page.getByLabel('读数范围').selectOption(run)
  await page.reload()
  await expect(page.getByLabel('Skill 与版本')).toHaveValue(key)
  await expect(page.getByLabel('读数范围')).toHaveValue(run)
  await expect(page.locator('.sample-trace')).toContainText(run)
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  await expect(page.getByLabel('读数范围')).toHaveValue(run)
  await page.reload()
  await expect(page.getByLabel('读数范围')).toHaveValue(run)
  await page.getByRole('navigation').getByRole('button').nth(1).click()
  await page.goBack()
  await expect(page.getByLabel('读数范围')).toHaveValue(run)
  await page.getByRole('button', { name: '刷新评估', exact: true }).click()
  await expect(page.getByLabel('读数范围')).toHaveValue(run)
  await page.goto(`/#${new URLSearchParams({ section: '版本评估', evaluation_source: 'api', evaluation_version: key, evaluation_scope: 'missing-run' })}`)
  await expect(page.getByRole('heading', { name: '所选版本或运行范围当前不可见' })).toBeVisible()
  await expect(page.locator('.metrics-grid')).toHaveCount(0)
  await expect(page.locator('.management-panel')).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-60', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.evaluation-panel').screenshot({ path: `../docs/reviews/ui/quality-round-60/evaluation-location-${width}.png`, animations: 'disabled' })
  }
  await page.getByRole('button', { name: '清除评估定位' }).click()
  await expect(page.getByLabel('读数范围')).toHaveValue('aggregate')
  await expect(page.getByLabel('Skill 与版本')).toHaveValue(versionKey(snapshot.versions[0]!))
})

test('版本对比同时核对启用、执行权限和当前评估快照', async ({ page }) => {
  await authenticatedSession(page)
  const config = { ...createManagementDemo(), source: 'ledger' }
  config.capabilities.version_comparison.enabled = true
  config.capabilities.version_comparison.can_execute = false
  const snapshot = evaluationFixture()
  snapshot.comparison_enabled = false
  await page.route('**/api/v27/management', route => route.fulfill({ json: config }))
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: snapshot }))
  await page.goto('/?evaluation=api#section=版本评估')
  const comparison = page.locator('.evaluation-comparison')
  await expect(comparison).toContainText('当前账号未获执行授权')
  await expect(page.getByLabel('基准版本')).toHaveCount(0)
  config.capabilities.version_comparison.can_execute = true
  await page.getByRole('button', { name: '刷新管理状态', exact: true }).click()
  await expect(comparison).toContainText('当前评估快照尚未确认')
  await expect(page.getByLabel('基准版本')).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-54', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await comparison.screenshot({ path: `../docs/reviews/ui/quality-round-54/comparison-pending-${width}.png`, animations: 'disabled' })
  }
  snapshot.comparison_enabled = true
  await comparison.getByRole('button', { name: '刷新评估快照', exact: true }).click()
  await page.getByLabel('基准版本').selectOption({ index: 1 })
  await page.getByLabel('候选版本').selectOption({ index: 2 })
  await expect(comparison.getByRole('table')).toContainText('-80 个百分点')
  config.capabilities.version_comparison.can_execute = false
  await page.getByRole('button', { name: '刷新管理状态', exact: true }).click()
  await expect(comparison.getByRole('table')).toHaveCount(0)
  await expect(comparison).toContainText('当前账号未获执行授权')
})

test('单次读数可追溯到授权运行查询，不猜测框架或伪造账本', async ({ page }) => {
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  let queried = ''
  const requests: URL[] = []
  await page.route('**/api/v27/runs?*', route => {
    requests.push(new URL(route.request().url()))
    queried = new URL(route.request().url()).searchParams.get('q') ?? ''
    return route.fulfill({ json: { source: 'ledger', items: [], next_cursor: null } })
  })
  await page.goto('/?evaluation=api')
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  await page.getByLabel('读数范围').selectOption('1.0-RUN-0')
  await expect(page.locator('.sample-trace')).toContainText('1.0-RUN-0')
  await mkdir('../docs/reviews/ui/quality-round-48', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.sample-trace').screenshot({ path: `../docs/reviews/ui/quality-round-48/sample-trace-${width}.png`, animations: 'disabled' })
  }
  await page.getByRole('button', { name: '查找运行账本', exact: true }).click()
  await expect(page.getByLabel('查询运行', { exact: true })).toHaveValue('1.0-RUN-0')
  await expect.poll(() => queried).toBe('1.0-RUN-0')
  await expect(page.locator('.service-run-history')).toContainText('当前条件下没有可见运行')
  await expect(page.locator('.run-detail')).toHaveCount(0)
  await page.getByLabel('服务运行框架').selectOption('LangGraph')
  await page.locator('.service-run-history select').nth(1).selectOption('closed')
  await page.getByRole('button', { name: '查询运行', exact: true }).click()
  await expect.poll(() => requests[requests.length - 1]?.searchParams.get('framework')).toBe('LangGraph')
  const previousCount = requests.length
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  await page.getByLabel('读数范围').selectOption('1.0-RUN-0')
  await page.getByRole('button', { name: '查找运行账本', exact: true }).click()
  await expect.poll(() => requests.length).toBeGreaterThan(previousCount)
  await expect(page.getByLabel('服务运行框架')).toHaveValue('全部框架')
  await expect(page.locator('.service-run-history select').nth(1)).toHaveValue('全部状态')
  expect(requests[requests.length - 1]?.searchParams.get('q')).toBe('1.0-RUN-0')
  expect(requests[requests.length - 1]?.searchParams.has('framework')).toBe(false)
  expect(requests[requests.length - 1]?.searchParams.has('state')).toBe(false)
})

test('版本评估显示可解释口径，未接入数据不产生通过结论', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-7', { recursive: true })
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  const workspace = page.locator('.governance-workspace')
  await expect(workspace.locator('.metrics-grid strong')).toHaveText(Array(8).fill('N/A'))
  await workspace.getByText('复现率', { exact: true }).locator('..').getByText('计算口径').click()
  await expect(workspace.getByText(/缺证据计为未复现，保留在分母内/)).toBeVisible()
  await workspace.getByText('哪些运行可以计入样本？').click()
  await expect(workspace.getByText(/shadow 条件约束实时拦截上线/)).toBeVisible()
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-7/evaluation-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.governance-workspace').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
})

test('服务读数区分聚合与运行，比较差值保留方向和不适用', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-7', { recursive: true })
  const config = { ...createManagementDemo(), source: 'ledger' }
  config.capabilities.version_comparison.enabled = true
  await page.route('**/api/v27/management', route => route.fulfill({ json: config }))
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  await page.goto('/?evaluation=api')
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  const workspace = page.locator('.governance-workspace')
  await expect(workspace.locator('.metrics-grid strong').nth(1)).toHaveText('80%')
  await expect(workspace.locator('.metrics-grid strong').nth(3)).toHaveText('不适用')
  await page.getByLabel('读数范围').selectOption('1.0-RUN-0')
  await expect(workspace.locator('.metrics-grid strong').nth(1)).toHaveText('0%')
  await expect(workspace.locator('.metrics-grid').getByText('分子 0 / 分母 2').first()).toBeVisible()
  await page.getByLabel('读数范围').selectOption('aggregate')
  await page.getByLabel('基准版本').selectOption({ index: 1 })
  await page.getByLabel('候选版本').selectOption({ index: 2 })
  await expect(page.getByRole('table')).toContainText('-80 个百分点')
  await expect(page.getByRole('table')).toContainText('-2 权重')
  await inspectState(page, 'evaluation-comparison', '.governance-workspace', 73)
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-7/comparison-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.governance-workspace').analyze()
  expect(result.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
  await page.getByLabel('候选版本').selectOption({ index: 3 })
  await expect(workspace.locator('.evaluation-comparison').getByRole('status')).toContainText('样本不足')
  await expect(page.getByRole('table')).toHaveCount(0)
  await inspectState(page, 'evaluation-insufficient', '.governance-workspace', 73)
  await page.getByLabel('Skill 与版本').selectOption({ index: 2 })
  await expect(workspace.locator('.metrics-grid strong')).toHaveText(Array(8).fill('N/A'))
  await page.getByLabel('读数范围').selectOption('1.2-RUN-1')
  await expect(workspace.locator('.metrics-grid strong').nth(1)).toHaveText('100%')
})

test('无权限与损坏快照阻止展示，重试空集合不伪造版本', async ({ page }) => {
  let status = 403, payload: unknown = {}
  await page.route('**/api/v27/evaluations', route => route.fulfill({ status, json: payload }))
  await page.goto('/?evaluation=api')
  await page.getByRole('navigation').getByRole('button').nth(4).click()
  const workspace = page.locator('.governance-workspace')
  await expect(workspace.getByRole('alert')).toContainText('无权')
  await expect(workspace.locator('.metrics-grid')).toHaveCount(0)
  await page.screenshot({ path: '../docs/reviews/ui/quality-round-7/forbidden-1280.png', fullPage: true, animations: 'disabled' })
  const bad = evaluationFixture()
  bad.versions[0].aggregate.aggregated_readings!.evidence_completeness.value = 0.99
  status = 200; payload = bad
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(workspace.getByRole('alert')).toContainText('口径')
  payload = { ...evaluationFixture(), versions: [], comparison_enabled: false }
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(workspace.getByRole('status').filter({ hasText: '暂无可评估版本' })).toBeVisible()
  await expect(workspace.locator('.metrics-grid strong')).toHaveText(Array(8).fill('N/A'))
})
