import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { authenticatedSession } from './helpers/authenticatedSession'

const scenarios = [
  { section: '工作台', query: 'workspace', endpoint: '**/api/v27/run-context', panel: '.run-create-panel', loading: '正在读取可用框架' },
  { section: '运行记录', query: 'runs', endpoint: '**/api/v27/runs?*', panel: '.service-run-history', loading: '正在查询运行' },
  { section: '人工审批', query: 'approval', endpoint: '**/api/v27/approvals', panel: '.approval-workspace', loading: '正在读取审批队列' },
  { section: '证据与债务', query: 'debt', endpoint: '**/api/v27/debts', panel: '.debt-workspace', loading: '正在读取债务' },
  { section: '版本评估', query: 'evaluation', endpoint: '**/api/v27/evaluations', panel: '.evaluation-panel', loading: '正在读取版本与运行样本' },
  { section: '设置', query: 'settings', endpoint: '**/api/v27/settings', panel: '.settings-panel', loading: '正在读取身份与配置' },
]
for (const scenario of scenarios) test(`${scenario.section}服务加载和失败状态四档无严重可访问性违规`, async ({ page }) => {
  test.setTimeout(120000)
  await authenticatedSession(page)
  let release = () => {}
  let waiting = new Promise<void>(resolve => { release = resolve })
  await page.route(scenario.endpoint, async route => { await waiting; await route.fulfill({ status: 503, json: {} }) })
  const directory = '../docs/reviews/ui/quality-round-72'
  await mkdir(directory, { recursive: true })
  const results: unknown[] = []
  for (const width of [1440, 1024, 390, 320]) {
    waiting = new Promise<void>(resolve => { release = resolve })
    await page.goto('about:blank')
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`/?${scenario.query}=api#${new URLSearchParams({ section: scenario.section })}`)
    await expect(page.locator(scenario.panel)).toContainText(scenario.loading)
    for (const state of ['loading', 'failure']) {
      if (state === 'failure') { release(); await expect(page.locator(scenario.panel).getByRole('alert').first()).toBeVisible() }
      await page.evaluate(() => document.fonts.ready)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      const scan = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
      const serious = scan.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))
      results.push({ width, state, violations: scan.violations.map(item => ({ id: item.id, impact: item.impact, targets: item.nodes.map(node => node.target) })) })
      expect(serious, `${scenario.section} ${width} ${state}`).toEqual([])
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.locator(scenario.panel).screenshot({ path: `${directory}/${scenario.query}-${state}-${width}.png`, animations: 'disabled' })
    }
  }
  await writeFile(`${directory}/${scenario.query}-a11y.json`, JSON.stringify(results, null, 2))
})

test('六板块服务拒绝访问四档保留可读错误，不回退演示结果', async ({ page }) => {
  test.setTimeout(120000)
  await authenticatedSession(page)
  for (const scenario of scenarios) await page.route(scenario.endpoint, route => route.fulfill({ status: 403, json: {} }))
  const directory = '../docs/reviews/ui/quality-round-65'
  await mkdir(directory, { recursive: true })
  const results: unknown[] = []
  for (const scenario of scenarios) {
    await page.goto('about:blank')
    await page.goto(`/?${scenario.query}=api#${new URLSearchParams({ section: scenario.section })}`)
    await expect(page.locator(scenario.panel).getByRole('alert').first()).toContainText(/无权|权限/)
    for (const width of [1440, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.evaluate(() => document.fonts.ready)
      const scan = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
      results.push({ section: scenario.section, width, violations: scan.violations.map(value => ({ id: value.id, impact: value.impact })) })
      expect(scan.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? ''))).toEqual([])
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.locator(scenario.panel).screenshot({ path: `${directory}/${scenario.query}-forbidden-${width}.png`, animations: 'disabled' })
    }
  }
  await writeFile(`${directory}/forbidden-a11y.json`, JSON.stringify(results, null, 2))
})
