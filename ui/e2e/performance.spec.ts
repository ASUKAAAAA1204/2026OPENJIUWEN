import { test, expect } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { preview, type PreviewServer } from 'vite'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import { createApprovalDemo } from '../src/components/workbench/approvals'
import { createDebtDemo } from '../src/components/workbench/debts'
import { createManagementDemo } from '../src/components/workbench/management'
import { evaluationFixture } from '../tests/fixtures/evaluations'
import { runContextFixture, lifecycleFixture } from '../tests/fixtures/lifecycle'
import { policyCatalogFixture, policySettingsFixture, policyReceiptFixture } from '../tests/fixtures/policies'
import { authenticatedSession } from './helpers/authenticatedSession'
let production: PreviewServer
test.beforeAll(async () => { production = await preview({ preview: { host: '127.0.0.1', port: 5274, strictPort: true } }) })
test.afterAll(async () => {
  if (!production) return
  if ('closeAllConnections' in production.httpServer) production.httpServer.closeAllConnections()
  await new Promise<void>((resolve, reject) => production.httpServer.close(error => error ? reject(error) : resolve()))
})
declare global {
  interface Window { __workbenchMetrics: { lcp: number; longTasks: number[]; cls: number; interaction: number; shifts: Array<{ value: number; nodes: string[] }> } }
}

test('生产七页面API四档加载、导航和核心操作达到预算', async ({ page, browserName, browser }) => {
  test.setTimeout(120000)
  const samples: Array<Record<string, unknown>> = []
  const directory = '../docs/reviews/ui/quality-round-72'
  await mkdir(directory, { recursive: true })
  const approvals = createApprovalDemo(Date.now() + 600000)
  await authenticatedSession(page)
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: runContextFixture() }))
  await page.route('**/api/v27/runs?*', route => route.fulfill({ json: { source: 'ledger', items: [lifecycleFixture().run], next_cursor: null } }))
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: approvals }))
  await page.route('**/api/v27/debts', route => route.fulfill({ json: { ...createDebtDemo(), source: 'ledger' } }))
  await page.route('**/api/v27/evaluations', route => route.fulfill({ json: evaluationFixture() }))
  await page.route('**/api/v27/management', route => route.fulfill({ json: { ...createManagementDemo(), source: 'ledger' } }))
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(policyCatalogFixture()) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: policyCatalogFixture() }))
  await page.route('**/api/v27/runs/create', route => route.fulfill({ json: { source: 'ledger', action: 'create', request_id: route.request().postDataJSON().request_id, audit_id: 'PERF-CREATE', created: true, snapshot: lifecycleFixture() } }))
  await page.route('**/api/v27/approvals/*/respond', route => {
    const item = approvals[0]
    return route.fulfill({ json: { ...item, status: 'approved', revision: 'perf-approved', response: { approver_id: 'PERF-REVIEWER', approve_ts: new Date().toISOString(), approved_args_digest: item.args_digest, approve_result: 'approve', content_hash: 'synthetic-fingerprint', note: '' } } })
  })
  await page.route('**/api/v27/debts/*/resolve', route => route.fulfill({ json: { ...createDebtDemo().items[0], status: 'resolved', resolved: { seq: 4, method: route.request().postDataJSON().method, evidence_ref: route.request().postDataJSON().evidence_ref, regate_success: null } } }))
  await page.route('**/api/v27/policies/activate', route => {
    const body = route.request().postDataJSON()
    return route.fulfill({ json: policyReceiptFixture(policyCatalogFixture(), body.request_id, body.reason) })
  })
  await page.route('**/api/v27/runs/RUN-PERFORMANCE/lifecycle', route => route.fulfill({ status: 403, json: {} }))
  const decisions = previewDecisions(createPreview('RUN-PERFORMANCE', '合成详情性能样本', 'openJiuwen'))
  await page.route('**/api/v27/runs/RUN-PERFORMANCE/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-PERFORMANCE', state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions, chain: { status: 'unverified', reason: 'synthetic performance fixture' } } }))
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await page.addInitScript(() => {
    const metrics = { longTasks: [] as number[], cls: 0, lcp: 0, interaction: 0, shifts: [] as Array<{ value: number; nodes: string[] }> }
    if (!['longtask', 'layout-shift', 'largest-contentful-paint', 'event'].every(type => PerformanceObserver.supportedEntryTypes.includes(type))) throw new Error('当前浏览器缺少必要性能观察器，不能将未测量记为零。')
    Object.defineProperty(window, '__workbenchMetrics', { value: metrics, configurable: true })
    new PerformanceObserver(list => metrics.longTasks.push(...list.getEntries().map(item => item.duration))).observe({ type: 'longtask', buffered: true })
    new PerformanceObserver(list => { for (const item of list.getEntries()) { const shift = item as PerformanceEntry & { hadRecentInput: boolean; value: number; sources?: Array<{ node?: Element }> }; if (!shift.hadRecentInput) { metrics.cls += shift.value; metrics.shifts.push({ value: shift.value, nodes: (shift.sources ?? []).map(source => source.node ? `${source.node.tagName}.${source.node.className}` : 'unknown') }) } } }).observe({ type: 'layout-shift', buffered: true })
    new PerformanceObserver(list => { const entries = list.getEntries(), entry = entries[entries.length - 1]; if (entry) metrics.lcp = entry.startTime }).observe({ type: 'largest-contentful-paint', buffered: true })
    const eventOptions: PerformanceObserverInit & { durationThreshold: number } = { type: 'event', buffered: true, durationThreshold: 16 }
    new PerformanceObserver(list => { for (const entry of list.getEntries()) metrics.interaction = Math.max(metrics.interaction, entry.duration) }).observe(eventOptions)
  })
  for (const width of [1440, 1024, 390, 320]) {
    for (const source of ['api']) {
    for (const section of ['工作台', '运行记录', '人工审批', '证据与债务', '版本评估', '设置', '运行详情']) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('about:blank')
    const started = performance.now()
    const hash = section === '运行详情' ? 'run=RUN-PERFORMANCE&framework=openJiuwen' : `section=${encodeURIComponent(section)}`
    const query = source === 'api' ? '?workspace=api&runs=api&approval=api&debt=api&evaluation=api&settings=api' : ''
    await page.goto(`/${query}#${hash}`, { waitUntil: 'load' })
    await expect(page.getByRole('heading', { name: section, exact: true })).toBeVisible()
    const readySelector: Record<string, string> = { 工作台: '.run-create-form', 运行记录: '.service-run-history .history-card', 人工审批: '.approval-actions', 证据与债务: '.debt-list .history-card', 版本评估: '.metrics-grid', 设置: '.settings-panel .approval-facts', 运行详情: '.decision-row' }
    if (source === 'api') await expect(page.locator(readySelector[section]).first()).toBeVisible()
    await expect(page.locator('script[src*="/@vite/client"]')).toHaveCount(0)
    await page.evaluate(() => document.fonts.ready)
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    await expect.poll(() => page.evaluate(() => (window as Window & { __workbenchMetrics: { lcp: number } }).__workbenchMetrics.lcp)).toBeGreaterThan(0)
    const loaded = await page.evaluate(() => ({ dom: (performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming)?.domContentLoadedEventEnd ?? 0, lcp: (window as Window & { __workbenchMetrics: { lcp: number } }).__workbenchMetrics.lcp }))
    const loadDuration = performance.now() - started
    let operationMs: number | null = null
    if (source === 'api') {
      let action: () => Promise<void>, verify: () => Promise<void>
      if (section === '工作台') {
        await page.getByLabel('业务任务标识', { exact: true }).fill('BUSINESS-42')
        await page.getByLabel('任务描述', { exact: true }).fill('核查项目文件并生成修改建议')
        await page.getByLabel('授权工作目录', { exact: true }).selectOption('workspace-1')
        await page.getByLabel('Skill 与版本', { exact: true }).selectOption(JSON.stringify(['code-assistant', '1.0']))
        await page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本').check()
        const button = page.getByRole('button', { name: '创建账本运行', exact: true })
        await button.scrollIntoViewIfNeeded()
        action = () => button.click()
        verify = () => expect(page.locator('.run-create-panel').getByRole('status')).toContainText('运行已登记')
      } else if (section === '人工审批') {
        await page.getByLabel('我已核对目标、参数和操作范围').check()
        const button = page.getByRole('button', { name: '批准请求', exact: true })
        await button.scrollIntoViewIfNeeded()
        action = () => button.click()
        verify = () => expect(page.locator('.approval-result')).toContainText('PERF-REVIEWER')
      } else if (section === '证据与债务') {
        await page.getByLabel('证据标识', { exact: true }).fill('perf:evidence')
        await page.getByLabel('内容指纹 SHA-256').fill('b'.repeat(64))
        await page.getByLabel('我已核对消解方法与证据范围').check()
        const button = page.getByRole('button', { name: '提交消解核验', exact: true })
        await button.scrollIntoViewIfNeeded()
        action = () => button.click()
        verify = () => expect(page.locator('.debt-workspace')).toContainText('服务端已返回消解记录')
      } else if (section === '设置') {
        await page.locator('.settings-panel').getByRole('button', { name: '策略表', exact: true }).click()
        await page.getByLabel('候选策略版本', { exact: true }).selectOption('policy-2.7.2')
        await page.getByLabel('策略切换原因', { exact: true }).fill('合成性能验收')
        await page.getByLabel('我已核对完整配置差异、启用范围与历史快照边界').check()
        const button = page.getByRole('button', { name: '确认切换策略版本', exact: true })
        await button.scrollIntoViewIfNeeded()
        action = () => button.click()
        verify = () => expect(page.locator('.settings-panel').getByRole('status').filter({ hasText: '已确认切换至' })).toContainText('policy-2.7.2')
      } else if (section === '版本评估') {
        await page.getByLabel('读数范围').scrollIntoViewIfNeeded()
        action = () => page.getByLabel('读数范围').selectOption('1.0-RUN-0').then(() => {})
        verify = () => expect(page.locator('.metrics-grid strong').nth(1)).toHaveText('0%')
      } else if (section === '运行详情') {
        await page.getByLabel('Gate 判定', { exact: true }).scrollIntoViewIfNeeded()
        action = () => page.getByLabel('Gate 判定', { exact: true }).selectOption('DENY').then(() => {})
        verify = () => expect(page.getByRole('heading', { name: '没有匹配的决策' })).toBeVisible()
      } else {
        const button = page.getByRole('button', { name: '查询运行', exact: true })
        await button.scrollIntoViewIfNeeded()
        action = async () => { const response = page.waitForResponse(value => value.url().includes('/api/v27/runs?')); await button.click(); await response }
        verify = () => expect(page.locator('.service-run-history .history-card')).toHaveCount(1)
      }
      const operationStart = performance.now()
      await action(); await verify()
      operationMs = performance.now() - operationStart
    }
    const navStart = performance.now()
    const destination = section === '运行记录' ? '工作台' : '运行记录'
    await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: new RegExp(destination) }).click()
    await expect(page.getByRole('heading', { name: destination, exact: true })).toBeVisible()
    const navDuration = performance.now() - navStart
    await page.waitForTimeout(250)
    const browserMetrics = await page.evaluate(() => {
    const metrics = (window as Window & { __workbenchMetrics: { longTasks: number[]; cls: number; interaction: number } }).__workbenchMetrics
      return { longTasks: metrics.longTasks, cls: metrics.cls, interaction: metrics.interaction, shifts: window.__workbenchMetrics.shifts }
    })
    const sample = { section, source, browserName, width, loadMs: loadDuration, domContentLoadedMs: loaded.dom, lcpMs: loaded.lcp, operationMs, navigationMs: navDuration, cls: browserMetrics.cls, shifts: browserMetrics.shifts, longTasks: browserMetrics.longTasks, eventDurationMs: browserMetrics.interaction }
    samples.push(sample)
    await writeFile(`${directory}/performance.json`, `${JSON.stringify({ mode: 'local-production-preview', at: new Date().toISOString(), browser: browser.version(), cache: 'disabled', thresholds: { domContentLoadedMs: 1500, lcpMs: 2500, operationMs: 200, navigationMs: 200, cls: 0.05, maxLongTaskMs: 100, eventDurationMs: 200 }, note: 'One observation per page/source/width, Chromium desktop local loopback; synthetic authorized service responses, no CPU/network throttling, no backend/model latency, no field INP or percentile claim. Operation duration starts with the control visible and includes Playwright overhead and the response/render assertion. All pages use HTTP API fixtures.', samples }, null, 2)}\n`)
    expect(sample.domContentLoadedMs, `${width}px DOMContentLoaded`).toBeLessThan(1500)
    expect(sample.lcpMs, `${width}px observed LCP`).toBeGreaterThan(0)
    expect(sample.lcpMs, `${width}px LCP`).toBeLessThan(2500)
    expect(sample.navigationMs, `${width}px navigation`).toBeLessThan(200)
    expect(sample.cls, `${width}px CLS`).toBeLessThan(0.05)
    expect(Math.max(0, ...browserMetrics.longTasks), `${width}px long task`).toBeLessThan(100)
    expect(sample.eventDurationMs, `${width}px event duration`).toBeLessThan(200)
    if (operationMs !== null) expect(operationMs, `${section} ${width}px operation`).toBeLessThan(200)
    }
    }
  }
})
