import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { authenticatedSession } from './helpers/authenticatedSession'
import AxeBuilder from '@axe-core/playwright'
import { inspectState } from './helpers/stateA11y'

// Synthetic state only; production requires the authorized execution-state endpoint.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v27/runs/*/execution-state?*', route => {
    const url = new URL(route.request().url())
    return route.fulfill({ json: { source: 'ledger', run_id: decodeURIComponent(url.pathname.split('/')[4]!), segment: Number(url.searchParams.get('segment')), revision: 'fixture-idle', status: 'idle', request_id: null } })
  })
})

test('持续接收事件和明确失败终态四档可读，失败后不自动重启', async ({ page }) => {
  await authenticatedSession(page)
  await page.route('**/api/v27/runs/RUN-LIVE-STATES/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-LIVE-STATES', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: 'synthetic stream state' } } }))
  // A controlled browser ReadableStream keeps the response open during each scan.
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    let active: ReadableStreamDefaultController<Uint8Array> | undefined
    const encode = (event: { seq: number; [key: string]: unknown }) => new TextEncoder().encode(`id: ${event.seq}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`)
    window.fetch = async (input, options) => {
      if (typeof input === 'string' && input.endsWith('/RUN-LIVE-STATES/execute')) {
        return new Response(new ReadableStream<Uint8Array>({ start(controller) {
          active = controller
          controller.enqueue(encode({ seq: 1, type: 'turn_started', turn_id: 'TURN-LIVE', run_id: 'RUN-LIVE-STATES', segment: 1 }))
          controller.enqueue(encode({ seq: 2, type: 'assistant_delta', turn_id: 'TURN-LIVE', text: '合成流：正在核对受控文件。' }))
        } }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return original(input, options)
    }
    Object.defineProperty(window, 'finishSyntheticExecution', { value: () => {
      active?.enqueue(encode({ seq: 3, type: 'turn_failed', turn_id: 'TURN-LIVE', outcome: 'known_failure', message: '合成工具服务确认失败，请核对账本。' }))
      active?.close()
    } })
  })
  await page.goto('/#run=RUN-LIVE-STATES&framework=openJiuwen')
  const panel = page.locator('.run-execution-panel')
  await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  await expect(panel.locator('.outline-label')).toHaveText('运行中')
  await expect(panel).toContainText('正在接收运行事件')
  await inspectState(page, 'execution-streaming', '.run-execution-panel', 73)
  await page.evaluate(() => (window as unknown as { finishSyntheticExecution: () => void }).finishSyntheticExecution())
  await expect(panel.locator('.outline-label')).toHaveText('本轮失败')
  await expect(panel).toContainText('合成工具服务确认失败')
  await expect(panel.getByRole('button', { name: /启动 Agent|重新连接/ })).toHaveCount(0)
  await inspectState(page, 'execution-known-failure', '.run-execution-panel', 73)
})

test('刷新后恢复服务端在途请求，先查状态再连接且不创建新请求', async ({ page }) => {
  await authenticatedSession(page)
  const runId = 'RUN-RESTORE-57'
  let knownRequest = '', starts = 0, stateReads = 0
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成恢复验收' } } }))
  await page.route(`**/api/v27/runs/${runId}/execution-state?*`, route => {
    stateReads++
    return route.fulfill({ json: { source: 'ledger', run_id: runId, segment: 1, revision: 'state-57', status: knownRequest ? 'active' : 'idle', request_id: knownRequest || null } })
  })
  await page.route(`**/api/v27/runs/${runId}/execute`, route => {
    const body = route.request().postDataJSON()
    expect(body.expected_execution_revision).toBe('state-57')
    starts++
    if (!knownRequest) knownRequest = body.request_id
    expect(body.request_id).toBe(knownRequest)
    const terminal = starts === 1 ? { seq: 2, type: 'turn_failed', turn_id: 'TURN-57', outcome: 'unknown', message: '合成连接终止，运行结果待查' } : { seq: 2, type: 'turn_completed', turn_id: 'TURN-57', run_state: 'open' }
    return route.fulfill({ contentType: 'text/event-stream', body: `id: 1\nevent: run_event\ndata: ${JSON.stringify({ seq: 1, type: 'turn_started', turn_id: 'TURN-57', run_id: runId, segment: 1 })}\n\nid: 2\nevent: run_event\ndata: ${JSON.stringify(terminal)}\n\n` })
  })
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  await expect(panel.locator('.outline-label')).toHaveText('结果待核对')
  await page.reload()
  await expect(panel.locator('.outline-label')).toHaveText('结果待核对')
  await expect(panel.getByRole('button', { name: '启动 Agent 运行', exact: true })).toHaveCount(0)
  expect(starts).toBe(1)
  await inspectState(page, 'execution-restored', '.run-execution-panel', 67)
  await mkdir('../docs/reviews/ui/quality-round-57', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-57/execution-restore-${width}.png`, animations: 'disabled' })
  }
  await panel.getByRole('button', { name: '重新连接同一执行', exact: true }).click()
  await expect(panel.locator('.outline-label')).toHaveText('本轮完成')
  expect(starts).toBe(2)
  expect(stateReads).toBe(2)
  await inspectState(page, 'execution-completed', '.run-execution-panel', 67)
})

test('恢复既有执行时连接被拒绝，不把拒绝续读当成原任务失败', async ({ page }) => {
  await authenticatedSession(page)
  const runId = 'RUN-RESTORE-DENIED'
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成恢复状态' } } }))
  await page.route(`**/api/v27/runs/${runId}/execution-state?*`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, segment: 1, revision: 'active-state', status: 'active', request_id: 'EXISTING-REQUEST' } }))
  await page.route(`**/api/v27/runs/${runId}/execute`, route => route.fulfill({ status: 403, json: {} }))
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await panel.getByRole('button', { name: '重新连接同一执行', exact: true }).click()
  await expect(panel.locator('.outline-label')).toHaveText('结果待核对')
  await expect(panel.getByRole('button', { name: '启动 Agent 运行', exact: true })).toHaveCount(0)
})

test('执行状态失败不显示启动入口，重读已完成状态仍不重复执行', async ({ page }) => {
  await authenticatedSession(page)
  const runId = 'RUN-STATE-57'
  let available = false, starts = 0
  await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成状态验收' } } }))
  await page.route(`**/api/v27/runs/${runId}/execution-state?*`, route => route.fulfill(available ? { json: { source: 'ledger', run_id: runId, segment: 1, revision: 'state-final', status: 'completed', request_id: 'REQ-FINAL' } } : { status: 503, json: {} }))
  await page.route(`**/api/v27/runs/${runId}/execute`, route => { starts++; return route.abort() })
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await expect(panel.getByRole('alert')).toContainText('尚未确认可启动')
  await inspectState(page, 'execution-state-failure', '.run-execution-panel', 73)
  await expect(panel.getByRole('button', { name: '启动 Agent 运行', exact: true })).toHaveCount(0)
  available = true
  await panel.getByRole('button', { name: '重新读取执行状态', exact: true }).click()
  await expect(panel.locator('.outline-label')).toHaveText('本轮完成')
  await expect(panel.getByRole('button', { name: /启动 Agent|重新连接/ })).toHaveCount(0)
  expect(starts).toBe(0)
})

test('超大运行事件停止展示并保留同请求核对入口', async ({ page }) => {
  await authenticatedSession(page)
  await page.route('**/api/v27/runs/RUN-LIMIT/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-LIMIT', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成验收数据' } } }))
  const requestIds: string[] = []
  await page.route('**/api/v27/runs/RUN-LIMIT/execute', route => {
    requestIds.push(route.request().postDataJSON().request_id)
    return route.fulfill({ contentType: 'text/event-stream', body: `data: ${'x'.repeat(65537)}` })
  })
  await page.goto('/#run=RUN-LIMIT&framework=openJiuwen')
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('单帧大小限制')
  await expect(panel.locator('.outline-label')).toHaveText('结果待核对')
  await expect(panel.getByRole('button', { name: '启动 Agent 运行', exact: true })).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-46', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-46/frame-limit-${width}.png`, animations: 'disabled' })
  }
  await panel.getByRole('button', { name: '重新连接同一执行', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('单帧大小限制')
  expect(requestIds).toHaveLength(2)
  expect(new Set(requestIds).size).toBe(1)
})

test('轮次结束保留工具未知结果告警，空白分片不导致断流', async ({ page }) => {
  await authenticatedSession(page)
  await page.route('**/api/v27/runs/RUN-UNKNOWN/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-UNKNOWN', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成验收数据' } } }))
  let requests = 0
  await page.route('**/api/v27/runs/RUN-UNKNOWN/execute', route => {
    requests++
    const events = [
      { seq: 1, type: 'turn_started', turn_id: 'TURN-U', run_id: 'RUN-UNKNOWN', segment: 1 },
      { seq: 2, type: 'assistant_delta', turn_id: 'TURN-U', text: '已提交受控操作。' },
      { seq: 3, type: 'assistant_delta', turn_id: 'TURN-U', text: '\n  ' },
      { seq: 4, type: 'assistant_delta', turn_id: 'TURN-U', text: '等待结果核对。' },
      { seq: 5, type: 'tool_result', turn_id: 'TURN-U', decision_id: 'DEC-U', status: 'UNKNOWN_OUTCOME', summary: '外部操作未返回可确认结果。', evidence_ref: null },
      { seq: 6, type: 'turn_completed', turn_id: 'TURN-U', run_state: 'open' },
    ]
    return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `id: ${event.seq}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('') })
  })
  await page.goto('/#run=RUN-UNKNOWN&framework=openJiuwen')
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  await expect(panel.locator('.outline-label')).toHaveText('结果待核对')
  await expect(panel.getByRole('alert')).toContainText('勿直接重复执行')
  await inspectState(page, 'execution-outcome-review', '.run-execution-panel', 73)
  await expect(panel.locator('.execution-output pre')).toHaveText('已提交受控操作。\n  等待结果核对。')
  await expect(panel.getByRole('button', { name: /启动 Agent|重新连接/ })).toHaveCount(0)
  expect(requests).toBe(1)
  await mkdir('../docs/reviews/ui/quality-round-43', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-43/unknown-outcome-${width}.png`, animations: 'disabled' })
  }
})

test('长运行预览截断有明确边界且保留终态', async ({ page }) => {
  await authenticatedSession(page)
  await page.route('**/api/v27/runs/RUN-LONG/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-LONG', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成验收数据' } } }))
  await page.route('**/api/v27/runs/RUN-LONG/execute', route => {
    const events: object[] = [{ seq: 1, type: 'turn_started', turn_id: 'TURN-L', run_id: 'RUN-LONG', segment: 1 }]
    for (let i = 0; i < 13; i++) events.push({ seq: events.length + 1, type: 'assistant_delta', turn_id: 'TURN-L', text: 'x'.repeat(4000) })
    for (let i = 0; i < 101; i++) events.push({ seq: events.length + 1, type: 'tool_result', turn_id: 'TURN-L', decision_id: `D-${i}`, status: 'EXEC_FINISHED', summary: '合成结果', evidence_ref: null })
    events.push({ seq: events.length + 1, type: 'turn_completed', turn_id: 'TURN-L', run_state: 'open' })
    return route.fulfill({ contentType: 'text/event-stream', body: events.map((event, index) => `id: ${index + 1}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('') })
  })
  await page.goto('/#run=RUN-LONG&framework=openJiuwen')
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  await expect(panel).toContainText('本轮完成')
  await expect(panel).toContainText('输出预览仅保留最近 50,000 个字符')
  await expect(panel).toContainText('本页仅展示最近 100 条运行事件')
  await expect(panel.locator('.execution-events li')).toHaveCount(100)
  expect((await panel.locator('.execution-output pre').textContent())?.length).toBe(50000)
  const output = panel.getByRole('region', { name: 'Agent 输出，可滚动阅读' })
  await output.focus()
  await expect(output).toBeFocused()
  await page.keyboard.press('PageDown')
  await expect.poll(() => output.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  const accessibility = await new AxeBuilder({ page }).include('.run-execution-panel').analyze()
  expect(accessibility.violations.filter(item => item.impact === 'serious' || item.impact === 'critical')).toEqual([])
  await mkdir('../docs/reviews/ui/quality-round-47', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.locator('.execution-output').screenshot({ path: `../docs/reviews/ui/quality-round-47/output-keyboard-${width}.png`, animations: 'disabled' })
  }
})

test('启动运行显示脱敏流、完成状态与四档截图', async ({ page }) => {
  await authenticatedSession(page)
  await mkdir('../docs/reviews/ui/quality-round-41', { recursive: true })
  let auditStatus = 200
  await page.route('**/api/v27/runs/RUN-EXEC/audit', route => route.fulfill({ status: auditStatus, json: { source: 'ledger', run_id: 'RUN-EXEC', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成验收数据' } } }))
  await page.route('**/api/v27/runs/RUN-EXEC/execute', route => {
    const body = route.request().postDataJSON()
    expect(route.request().headers()['idempotency-key']).toBe(body.request_id)
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    const events = [
      { seq: 1, type: 'turn_started', turn_id: 'TURN-EXEC', run_id: 'RUN-EXEC', segment: 1 },
      { seq: 2, type: 'assistant_delta', turn_id: 'TURN-EXEC', text: '已读取受控工作目录，正在核对文档。' },
      { seq: 3, type: 'tool_intent', turn_id: 'TURN-EXEC', decision_id: 'DEC-1', decision_class: 'file_write', tool_name: 'file_write', gate: 'DENY', summary: '写入被策略拒绝，未执行。', approval_id: null },
      { seq: 4, type: 'turn_completed', turn_id: 'TURN-EXEC', run_state: 'open' },
    ]
    return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `id: ${event.seq}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('') })
  })
  await page.goto('/#run=RUN-EXEC&framework=openJiuwen')
  await page.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await expect(panel).toContainText('本轮完成')
  await expect(panel).toContainText('写入被策略拒绝，未执行')
  await expect(panel.locator('.execution-output')).toContainText('正在核对文档')
  await page.getByRole('button', { name: '刷新账本', exact: true }).click()
  await expect(panel).toContainText('本轮完成')
  await expect(panel.locator('.execution-output')).toContainText('正在核对文档')
  await expect(panel.getByRole('button', { name: '启动 Agent 运行', exact: true })).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-44', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-41/execution-${width}.png`, animations: 'disabled' })
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-44/execution-retained-${width}.png`, animations: 'disabled' })
  }
  auditStatus = 500
  await page.getByRole('button', { name: '刷新账本', exact: true }).click()
  await expect(page.getByRole('heading', { name: '暂时无法加载' })).toBeVisible()
  await expect(panel).toHaveCount(0)
  await mkdir('../docs/reviews/ui/quality-round-45', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../docs/reviews/ui/quality-round-45/network-error-${width}.png`, fullPage: true, animations: 'disabled' })
  }
  auditStatus = 200
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(panel).toContainText('本轮完成')
  await expect(panel.locator('.execution-output')).toContainText('正在核对文档')
  for (const status of [401, 403, 404]) {
    auditStatus = status
    await page.getByRole('button', { name: '刷新账本', exact: true }).click()
    await expect(page.locator('.resource-error')).toBeVisible()
    await expect(panel).toHaveCount(0)
    if (status === 403) {
      for (const width of [1440, 1024, 390, 320]) {
        await page.setViewportSize({ width, height: 900 })
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: `../docs/reviews/ui/quality-round-45/permission-cleared-${width}.png`, fullPage: true, animations: 'disabled' })
      }
    }
    auditStatus = 200
    await page.getByRole('button', { name: '重新读取', exact: true }).click()
    await expect(panel).toContainText('未启动')
    await expect(panel.locator('.execution-output')).toHaveCount(0)
    await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
    await expect(panel.locator('.execution-output')).toContainText('正在核对文档')
  }
})

test('ASK 暂停运行流进入精确审批，批准后同一流继续并释放导航', async ({ page }) => {
  await authenticatedSession(page)
  await mkdir('../docs/reviews/ui/quality-round-41', { recursive: true })
  const approval = {
    approval_id: 'APP-EXEC-1', decision_id: 'DEC-EXEC-1', run_id: 'RUN-ASK', title: '写入受控配置', tool_name: 'file_write', risk: 'high',
    args_digest: '{"path":"workspace/config.yml"}', scope: '仅当前授权工作目录', requested_at: '2026-11-19T19:00:00Z', expires_at: '2026-11-19T21:00:00Z', revision: 'APP-REV-1',
    status: 'pending', can_respond: true, timeout_policy: 'DENY', response: null,
  }
  let release: () => void = () => {}
  const approvalWait = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/runs/RUN-ASK/audit', route => route.fulfill({ json: { source: 'ledger', run_id: 'RUN-ASK', state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成验收数据' } } }))
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [approval] }))
  await page.route('**/api/v27/runs/RUN-ASK/execute', async route => {
    const body = route.request().postDataJSON()
    expect(route.request().headers()['idempotency-key']).toBe(body.request_id)
    const start = { seq: 1, type: 'turn_started', turn_id: 'TURN-ASK', run_id: 'RUN-ASK', segment: 1 }
    const intent = { seq: 2, type: 'tool_intent', turn_id: 'TURN-ASK', decision_id: 'DEC-EXEC-1', decision_class: 'file_write', tool_name: 'file_write', gate: 'ASK', summary: '等待人工核对后才可写入。', approval_id: 'APP-EXEC-1' }
    const ask = { seq: 3, type: 'approval_requested', turn_id: 'TURN-ASK', approval_id: 'APP-EXEC-1', decision_id: 'DEC-EXEC-1', expires_at: approval.expires_at }
    const events = [start, intent, ask].map(event => `id: ${event.seq}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('')
    if (!route.request().headers()['last-event-id']) return route.fulfill({ contentType: 'text/event-stream', body: events })
    expect(route.request().headers()['last-event-id']).toBe('3')
    await approvalWait
    const tail = [
      { seq: 4, type: 'tool_result', turn_id: 'TURN-ASK', decision_id: 'DEC-EXEC-1', status: 'EXEC_FINISHED', summary: '服务端批准并完成受控操作。', evidence_ref: 'EVIDENCE-1' },
      { seq: 5, type: 'turn_completed', turn_id: 'TURN-ASK', run_state: 'open' },
    ].map(event => `id: ${event.seq}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('')
    return route.fulfill({ contentType: 'text/event-stream', body: tail })
  })
  await page.route('**/api/v27/approvals/APP-EXEC-1/respond', async route => {
    const body = route.request().postDataJSON()
    expect(body.approved_args_digest).toBe(approval.args_digest)
    expect(body.expected_revision).toBe(approval.revision)
    return route.fulfill({ json: { ...approval, status: 'approved', revision: 'APP-REV-2', response: { approver_id: 'reviewer-1', approve_ts: '2026-11-19T20:00:00Z', approved_args_digest: approval.args_digest, approve_result: 'approve', content_hash: 'f'.repeat(64), note: body.note } } })
  })
  await page.goto('/#run=RUN-ASK&framework=openJiuwen')
  const panel = page.getByRole('region', { name: 'Agent 运行控制' })
  await panel.getByRole('button', { name: '启动 Agent 运行', exact: true }).click()
  await expect(page.locator('.workbench-nav nav button').nth(2)).toHaveAttribute('aria-current', 'page')
  await expect(page.locator('.approval-workspace')).toContainText('运行 RUN-ASK 正在等待此审批')
  await expect(page.locator('.approval-card')).toContainText('APP-EXEC-1')
  await expect(page.locator('.approval-card')).toContainText('DEC-EXEC-1')
  await page.getByRole('button', { name: '返回运行执行详情' }).click()
  await expect(page.locator('.run-execution-panel')).toContainText('等待人工审批 APP-EXEC-1')
  await inspectState(page, 'execution-ask', '.run-execution-panel', 73)
  await page.getByRole('button', { name: '查看并处理此审批' }).click()
  await expect(page.locator('.approval-workspace')).toContainText('运行 RUN-ASK 正在等待此审批')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.approval-card').screenshot({ path: `../docs/reviews/ui/quality-round-41/approval-handoff-${width}.png`, animations: 'disabled' })
  }
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByLabel('审批说明').fill('核对目标路径及策略版本后批准')
  await page.getByRole('button', { name: '批准请求', exact: true }).click()
  await expect(page.locator('.approval-result')).toContainText('已批准')
  release()
  await expect(page.locator('.approval-workspace')).not.toContainText('正在等待此审批')
  await expect(page.getByRole('button', { name: '查看关联运行详情' })).toBeVisible()
  await expect(page.locator('.approval-result')).toContainText('工具执行结果需在运行账本中核查')
  await mkdir('../docs/reviews/ui/quality-round-42', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.approval-workspace').screenshot({ path: `../docs/reviews/ui/quality-round-42/approval-settled-${width}.png`, animations: 'disabled' })
  }
  await page.getByRole('button', { name: '查看关联运行详情' }).click()
  await expect(page.locator('.run-execution-panel')).toContainText('本轮完成')
  await expect(page.locator('.run-execution-panel')).toContainText('服务端批准并完成受控操作')
  await expect(page.locator('.workbench-nav nav button').nth(1)).toHaveAttribute('aria-current', 'page')
  await expect.poll(() => page.url()).toContain('run=RUN-ASK')
})
