import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { createRunFixture, lifecycleFixture, runContextFixture } from '../tests/fixtures/lifecycle'
import { authenticatedSession } from './helpers/authenticatedSession'
import { inspectState } from './helpers/stateA11y'
async function fillCreate(page: Page) {
  await page.getByLabel('业务任务标识', { exact: true }).fill(createRunFixture.task_id)
  await page.getByLabel('任务描述', { exact: true }).fill(createRunFixture.title)
  await page.getByLabel('授权工作目录', { exact: true }).selectOption(createRunFixture.workspace_id)
  await page.getByLabel('Skill 与版本', { exact: true }).selectOption(JSON.stringify([createRunFixture.skill_id, createRunFixture.skill_version]))
  await page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本').check()
}

test('创建范围变更清除旧确认，未核对新范围不提交', async ({ page }) => {
  await authenticatedSession(page)
  const context = runContextFixture()
  context.workspaces.push({ id: 'workspace-2', path: 'D:/controlled/second' })
  context.skills.push({ id: 'code-assistant', version: '2.0' })
  let posts = 0
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: context }))
  await page.route('**/api/v27/runs/create', route => { posts++; return route.fulfill({ status: 409, json: {} }) })
  await page.goto('/?workspace=api')
  await fillCreate(page)
  const confirmation = page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本')
  const submit = page.getByRole('button', { name: '创建账本运行', exact: true })
  for (const change of [
    () => page.getByLabel('业务任务标识', { exact: true }).fill('BUSINESS-NEW'),
    () => page.getByLabel('任务描述', { exact: true }).fill('新任务范围'),
    () => page.getByLabel('授权工作目录', { exact: true }).selectOption('workspace-2'),
    () => page.getByLabel('账本运行框架', { exact: true }).selectOption('LangGraph'),
    () => page.getByLabel('Skill 与版本', { exact: true }).selectOption(JSON.stringify(['code-assistant', '2.0'])),
  ]) {
    await expect(submit).toBeEnabled()
    await change()
    await expect(confirmation).not.toBeChecked()
    await expect(submit).toBeDisabled()
    expect(posts).toBe(0)
    await confirmation.check()
  }
  await submit.click()
  await expect(page.locator('.run-create-panel').getByRole('alert')).toContainText('已变化')
  expect(posts).toBe(1)
})

test('创建配置缺少资源时列出原因，不把空配置当可执行', async ({ page }) => {
  await authenticatedSession(page)
  const context = runContextFixture()
  context.workspaces = []; context.skills = []
  context.frameworks.forEach(item => { item.available = false; item.reason = '合成未接入状态' })
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: context }))
  await page.goto('/?workspace=api')
  const panel = page.locator('.run-create-panel')
  await expect(panel.getByRole('status')).toContainText('尚无授权工作目录')
  await expect(panel.getByRole('status')).toContainText('尚无可选 Skill 版本')
  await expect(panel.getByRole('status')).toContainText('三个框架均不可用')
  await page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本').check()
  await expect(page.getByRole('button', { name: '创建账本运行', exact: true })).toBeDisabled()
  await mkdir('../docs/reviews/ui/quality-round-55', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-55/create-unavailable-${width}.png`, animations: 'disabled' })
  }
  const result = await new AxeBuilder({ page }).include('.run-create-panel').analyze()
  expect(result.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  context.workspaces = runContextFixture().workspaces; context.skills = runContextFixture().skills
  context.frameworks[1].available = true
  await page.getByRole('button', { name: '刷新运行配置', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('当前所选框架不可用')
  await page.getByLabel('账本运行框架', { exact: true }).selectOption('LangGraph')
  await expect(panel.getByText('创建条件尚未齐备')).toHaveCount(0)
  await expect(page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本')).not.toBeChecked()
})
test('创建、两次中断恢复保持同一运行，关闭后只读且可刷新恢复', async ({ page }) => {
  test.setTimeout(60000)
  await mkdir('../docs/reviews/ui/quality-round-17', { recursive: true })
  await authenticatedSession(page)
  const context = runContextFixture(), snapshot = lifecycleFixture()
  let creates = 0, resumes = 0, closes = 0
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: context }))
  await page.route('**/api/v27/runs/create', route => {
    creates++
    const body = route.request().postDataJSON()
    expect(body.task_id).toBe(createRunFixture.task_id)
    expect(body.expected_context_revision).toBe(context.revision)
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    expect(route.request().headers()['idempotency-key']).toBe(body.request_id)
    return route.fulfill({ json: { source: 'ledger', action: 'create', request_id: body.request_id, audit_id: 'AUDIT-CREATE', created: true, snapshot } })
  })
  await page.route('**/api/v27/runs/*/audit', route => route.fulfill({ json: { source: 'ledger', run_id: snapshot.run.id, state: snapshot.run.state, segment: snapshot.run.segment, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成事件，不代表真实账本验证通过' } } }))
  await page.route('**/api/v27/runs/*/lifecycle', route => route.fulfill({ json: snapshot }))
  await page.route('**/api/v27/runs/*/resume', route => {
    const body = route.request().postDataJSON()
    expect(body.expected_segment).toBe(snapshot.run.segment)
    expect(body.expected_revision).toBe(snapshot.revision)
    expect(body.expected_state).toBe('open')
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    resumes++; snapshot.run.segment++; snapshot.revision = `resumed-${resumes}`; snapshot.can_resume = false; snapshot.reason = '此段已登记，无新中断；不会重复恢复。'
    return route.fulfill({ json: { source: 'ledger', action: 'resume', request_id: body.request_id, audit_id: `AUDIT-RESUME-${resumes}`, created: false, snapshot } })
  })
  await page.route('**/api/v27/runs/*/close', route => {
    closes++
    const body = route.request().postDataJSON()
    expect(body.expected_segment).toBe(3)
    expect(body.expected_revision).toBe(snapshot.revision)
    snapshot.run.state = 'closed'; snapshot.run.completed_at = '2026-11-19T13:00:00Z'; snapshot.can_close = false; snapshot.can_resume = false; snapshot.revision = 'closed-1'
    return route.fulfill({ json: { source: 'ledger', action: 'close', request_id: body.request_id, audit_id: 'AUDIT-CLOSE', created: false, snapshot } })
  })
  await page.goto('/?workspace=api')
  await fillCreate(page)
  await expect(page.locator('.inspector:visible')).toHaveCount(0)
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.run-create-panel').screenshot({ path: `../docs/reviews/ui/quality-round-17/create-${width}.png`, animations: 'disabled' })
  }
  const accessibility = await new AxeBuilder({ page }).include('.run-create-panel').analyze()
  expect(accessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  await page.getByRole('button', { name: '创建账本运行', exact: true }).click()
  await expect(page.locator('.run-create-panel').getByRole('status')).toContainText('不代表模型或工具已经执行')
  await inspectState(page, 'run-created', '.run-create-panel', 71)
  await page.getByRole('button', { name: '查看已登记运行' }).click()
  await expect(page).toHaveURL(/#run=RUN-LIFECYCLE-42/)
  await page.locator('.run-lifecycle-panel summary').click()
  await expect(page.locator('.run-lifecycle-panel')).toContainText('第 1 段')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.run-lifecycle-panel').screenshot({ path: `../docs/reviews/ui/quality-round-17/lifecycle-${width}.png`, animations: 'disabled' })
  }
  for (let count = 1; count <= 2; count++) {
    if (count === 2) {
      snapshot.can_resume = true; snapshot.revision = 'interrupted-again'; snapshot.reason = '第二次中断，无在途工具。'
      await page.locator('.run-lifecycle-panel summary').click()
      await page.getByRole('button', { name: '重新读取生命周期', exact: true }).click()
    }
    await page.getByLabel('生命周期操作原因').fill('人工核对中断记录后恢复')
    await page.getByLabel('我已核对当前运行、段号和操作影响').check()
    await page.getByRole('button', { name: '恢复运行段', exact: true }).click()
    await expect(page.locator('.run-detail .inline-feedback')).toContainText(`第 ${count + 1} 段`)
    await expect(page.locator('.run-lifecycle')).toContainText(`segment ${count + 1}`)
    if (count === 1) await inspectState(page, 'run-resumed', '.run-detail', 71)
  }
  await page.locator('.run-lifecycle-panel summary').click()
  await page.getByLabel('生命周期操作原因').fill('业务任务完成，关闭账本写入')
  await page.getByLabel('我已核对当前运行、段号和操作影响').check()
  await page.getByRole('button', { name: '关闭运行', exact: true }).click()
  await expect(page.locator('.run-lifecycle')).toContainText('closed')
  await page.reload()
  await page.locator('.run-lifecycle-panel summary').click()
  await expect(page.locator('.run-lifecycle-panel')).toContainText('此运行已关闭，不能继续写入或恢复')
  await expect(page.locator('.run-lifecycle-panel form')).toHaveCount(0)
  await inspectState(page, 'run-closed-readonly', '.run-lifecycle-panel', 71)
  expect({ creates, resumes, closes }).toEqual({ creates: 1, resumes: 2, closes: 1 })
})

test('重复任务返回原运行，关闭状态不允许恢复', async ({ page }) => {
  await authenticatedSession(page)
  const snapshot = lifecycleFixture()
  snapshot.run.segment = 4; snapshot.run.state = 'closed'; snapshot.run.completed_at = '2026-11-19T13:00:00Z'; snapshot.can_close = false; snapshot.can_resume = false
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: runContextFixture() }))
  await page.route('**/api/v27/runs/create', route => route.fulfill({ json: { source: 'ledger', action: 'create', request_id: route.request().postDataJSON().request_id, audit_id: 'AUDIT-EXISTING', created: false, snapshot } }))
  await page.goto('/?workspace=api')
  await fillCreate(page)
  await page.getByRole('button', { name: '创建账本运行', exact: true }).click()
  await expect(page.locator('.run-create-panel').getByRole('status')).toContainText('未重复开账')
  await expect(page.getByRole('button', { name: '创建账本运行', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  await expect(page.getByLabel('业务任务标识', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('任务描述', { exact: true })).toHaveValue('')
})

test('创建结果未知时停止重复提交，可按原任务标识查询核对', async ({ page }) => {
  await authenticatedSession(page)
  let posts = 0
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: runContextFixture() }))
  await page.route('**/api/v27/runs/create', route => { posts++; return route.abort('failed') })
  await page.route('**/api/v27/runs?*', route => {
    expect(new URL(route.request().url()).searchParams.get('q')).toBe(createRunFixture.task_id)
    return route.fulfill({ json: { source: 'ledger', items: [], next_cursor: null } })
  })
  await page.goto('/?workspace=api')
  await fillCreate(page)
  await page.getByRole('button', { name: '创建账本运行', exact: true }).click()
  await expect(page.locator('.run-create-panel').getByRole('alert')).toBeVisible()
  await expect(page.getByRole('button', { name: '创建账本运行', exact: true })).toBeDisabled()
  expect(posts).toBe(1)
  await page.getByRole('button', { name: '按任务查询运行记录' }).click()
  await expect(page.locator('.run-history').getByLabel('运行来源')).toHaveValue('api')
  await expect(page.locator('.service-run-history').getByLabel('查询运行', { exact: true })).toHaveValue(createRunFixture.task_id)
})

test('账本创建等待回执时阻止导航和浏览器返回，成功后恢复并保存四档截图', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-40', { recursive: true })
  await authenticatedSession(page)
  const context = runContextFixture(), snapshot = lifecycleFixture()
  let release: () => void = () => {}
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/run-context', route => route.fulfill({ json: context }))
  await page.route('**/api/v27/runs/create', async route => {
    const body = route.request().postDataJSON()
    expect(body.task_id).toBe(createRunFixture.task_id)
    await waiting
    return route.fulfill({ json: { source: 'ledger', action: 'create', request_id: body.request_id, audit_id: 'AUDIT-CREATE-WAIT', created: true, snapshot } })
  })
  await page.goto('/?workspace=api')
  await fillCreate(page)
  await page.getByRole('button', { name: '创建账本运行', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在登记运行…', exact: true })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: '任务来源', exact: true })).toBeDisabled()
  const busyHash = new URL(page.url()).hash
  await page.locator('.workbench-nav nav button').nth(1).click()
  await expect(page.locator('.demo-notice')).toContainText('运行操作正在等待回执')
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).click()
  await expect(page.getByLabel('业务任务标识', { exact: true })).toHaveValue(createRunFixture.task_id)
  await page.evaluate(() => { history.pushState(null, '', '#section=人工审批'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect.poll(() => new URL(page.url()).hash).toBe(busyHash)
  await expect(page.locator('.demo-notice')).toContainText('运行操作正在等待回执')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.demo-notice').screenshot({ path: `../docs/reviews/ui/quality-round-40/run-create-navigation-${width}.png`, animations: 'disabled' })
  }
  release()
  await expect(page.locator('.run-create-panel').getByRole('status')).toContainText('运行已登记')
  await page.locator('.workbench-nav nav button').nth(1).click()
  await expect(page.locator('.workbench-nav nav button').nth(1)).toHaveAttribute('aria-current', 'page')
})

test('快照不一致与关闭冲突均禁止继续写入，刷新重新获取权限', async ({ page }) => {
  await authenticatedSession(page)
  const snapshot = lifecycleFixture()
  let posts = 0
  await page.route('**/api/v27/runs/*/audit', route => route.fulfill({ json: { source: 'ledger', run_id: snapshot.run.id, state: 'open', segment: 2, parent_run_id: null, child_run_ids: [], decisions: [], chain: { status: 'unverified', reason: '合成快照' } } }))
  await page.route('**/api/v27/runs/*/lifecycle', route => route.fulfill({ json: snapshot }))
  await page.route('**/api/v27/runs/*/close', route => { posts++; return route.fulfill({ status: 409, json: {} }) })
  await page.goto('/#run=RUN-LIFECYCLE-42&framework=openJiuwen')
  await page.locator('.run-lifecycle-panel summary').click()
  const panel = page.locator('.run-lifecycle-panel')
  await expect(panel.getByRole('alert')).toContainText('快照不一致')
  await expect(panel.locator('form')).toHaveCount(0)
  snapshot.run.segment = 2; snapshot.can_resume = false
  await panel.getByRole('button', { name: '刷新账本并重新核对' }).click()
  await page.locator('.run-lifecycle-panel summary').click()
  await page.getByLabel('生命周期操作原因').fill('核对后申请关闭')
  await page.getByLabel('我已核对当前运行、段号和操作影响').check()
  await panel.getByRole('button', { name: '关闭运行', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('已变化')
  await expect(panel.getByRole('button', { name: '关闭运行', exact: true })).toBeDisabled()
  expect(posts).toBe(1)
  snapshot.can_close = false; snapshot.reason = '服务端撤销操作权限。'
  await panel.getByRole('button', { name: '刷新账本并重新核对' }).click()
  await page.locator('.run-lifecycle-panel summary').click()
  await expect(panel).toContainText('当前没有可用的生命周期写操作')
  await expect(panel.locator('form')).toHaveCount(0)
  expect(posts).toBe(1)
})
