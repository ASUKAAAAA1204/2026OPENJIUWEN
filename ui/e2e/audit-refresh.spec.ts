import { test, expect, type Route } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { lifecycleFixture } from '../tests/fixtures/lifecycle'
import { authenticatedSession } from './helpers/authenticatedSession'
import { previewDecisions } from '../src/components/workbench/audit'
import { createPreview } from '../src/components/workbench/model'
import { inspectState } from './helpers/stateA11y'

test('主动刷新读取新快照，生命周期提交中阻止刷新，权限失败清除旧记录', async ({ page }) => {
  await authenticatedSession(page)
  const snapshot = lifecycleFixture(), runId = snapshot.run.id
  let reads = 0, denied = false, held: Route | undefined
  const base = previewDecisions(createPreview(runId, 'refresh', 'openJiuwen'))[1]
  await page.route(`**/api/v27/runs/${runId}/audit`, route => { reads++; return denied ? route.fulfill({ status: 403, json: {} }) : route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'open', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [{ ...base, decision_id: `REFRESH-${reads}` }], chain: { status: 'unverified', reason: 'fixture' } } }) })
  await page.route(`**/api/v27/runs/${runId}/lifecycle`, route => route.fulfill({ json: snapshot }))
  await page.route(`**/api/v27/runs/${runId}/close`, route => { held = route })
  await page.goto(`/#run=${runId}&framework=openJiuwen`)
  await expect(page.locator('.decision-row')).toContainText('REFRESH-1')
  const refresh = page.getByRole('button', { name: '刷新账本', exact: true })
  await refresh.click()
  await expect(page.locator('.decision-row')).toContainText('REFRESH-2')
  await page.locator('.run-lifecycle-panel summary').click()
  await page.getByLabel('生命周期操作原因').fill('核对后结束运行')
  await page.getByLabel('我已核对当前运行、段号和操作影响', { exact: true }).check()
  await page.getByRole('button', { name: '关闭运行', exact: true }).click()
  await expect.poll(() => !!held).toBe(true)
  await expect(refresh).toBeDisabled()
  expect(reads).toBe(2)
  await mkdir('../docs/reviews/ui/quality-round-30', { recursive: true })
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.run-detail .run-toolbar').screenshot({ path: `../docs/reviews/ui/quality-round-30/refresh-${width}.png`, animations: 'disabled' })
  }
  await held!.fulfill({ status: 500, json: {} })
  await expect(refresh).toBeEnabled()
  denied = true
  await refresh.click()
  await expect(page.getByRole('heading', { name: '访问受限' })).toBeVisible()
  await expect(page.locator('.decision-row')).toHaveCount(0)
  await expect(page.locator('.run-lifecycle-panel')).toHaveCount(0)
  expect(reads).toBe(3)
})

for (const action of ['export', 'replay'] as const) {
  test(`${action} 等待服务时锁定账本刷新，失败后恢复刷新入口`, async ({ page }) => {
    await authenticatedSession(page)
    const runId = `RUN-REFRESH-${action}`
    const decision = previewDecisions(createPreview(runId, 'refresh', 'openJiuwen'))[1]
    let held: Route | undefined
    await page.route(`**/api/v27/runs/${runId}/audit`, route => route.fulfill({ json: { source: 'ledger', run_id: runId, state: 'closed', segment: 1, parent_run_id: null, child_run_ids: [], decisions: [decision], chain: { status: 'unverified', reason: 'fixture' } } }))
    await page.route(`**/api/v27/runs/${runId}/${action}`, route => { held = route })
    await page.goto(`/#run=${runId}&framework=openJiuwen`)
    const refresh = page.getByRole('button', { name: '刷新账本', exact: true })
    if (action === 'export') {
      await page.locator('.ledger-export summary').click()
      await page.getByLabel('我已确认此运行的导出范围和文件保存位置').check()
      await page.getByRole('button', { name: '申请账本导出', exact: true }).click()
    } else {
      await page.getByRole('button', { name: '回放', exact: true }).click()
      await page.getByLabel('我已核对运行与证据快照，同意发起只读回放').check()
      await page.getByRole('button', { name: '发起服务端回放', exact: true }).click()
    }
    await expect.poll(() => !!held).toBe(true)
    await expect(refresh).toBeDisabled()
    await inspectState(page, `${action}-waiting`, '.run-detail', 68)
    await page.getByRole('button', { name: '← 返回运行记录', exact: true }).click()
    await expect(page.getByRole('heading', { name: '运行详情', exact: true })).toBeVisible()
    await page.getByRole('navigation', { name: '主导航' }).getByRole('button').first().click()
    await expect(page.getByRole('heading', { name: '运行详情', exact: true })).toBeVisible()
    await expect(page.locator('.demo-notice')).toContainText('运行操作正在等待回执')
    await page.getByRole('link', { name: '旧版 Studio ↗' }).click()
    await expect(page.locator('.run-detail')).toBeVisible()
    await page.evaluate(() => { const current = location.href; history.replaceState(null, '', '#section=工作台'); history.pushState(null, '', current); history.back() })
    await expect(page).toHaveURL(new RegExp(`#run=${runId}`))
    await expect(page.locator('.run-detail')).toBeVisible()
    const preventsUnload = await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); dispatchEvent(event); return event.defaultPrevented })
    expect(preventsUnload).toBe(true)
    if (action === 'replay') {
      await mkdir('../docs/reviews/ui/quality-round-32', { recursive: true })
      for (const width of [1440, 1024, 390, 320]) {
        await page.setViewportSize({ width, height: 900 })
        await page.locator('.demo-notice').screenshot({ path: `../docs/reviews/ui/quality-round-32/navigation-${width}.png`, animations: 'disabled' })
      }
    }
    await held!.fulfill({ status: 500, json: {} })
    await expect(refresh).toBeEnabled()
    await inspectState(page, `${action}-service-failure`, '.run-detail', 68)
    await page.getByRole('button', { name: '← 返回运行记录', exact: true }).click()
    await expect(page.locator('.run-history')).toBeVisible()
  })
}
