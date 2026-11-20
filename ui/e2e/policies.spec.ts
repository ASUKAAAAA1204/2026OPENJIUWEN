import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { policyCatalogFixture, policyReceiptFixture, policySettingsFixture } from '../tests/fixtures/policies'
import { authenticatedSession } from './helpers/authenticatedSession'
import { inspectState } from './helpers/stateA11y'
async function openPolicies(page: Page) {
  await page.goto('/?settings=api')
  await page.getByRole('button', { name: '⚙ 设置' }).click()
  await page.locator('.settings-panel').getByRole('button', { name: '策略表', exact: true }).click()
}
async function confirmSwitch(page: Page, version: string, reason: string) {
  await page.getByLabel('候选策略版本', { exact: true }).selectOption(version)
  await page.getByLabel('策略切换原因', { exact: true }).fill(reason)
  await page.getByLabel('我已核对完整配置差异、启用范围与历史快照边界').check()
  await page.getByRole('button', { name: '确认切换策略版本', exact: true }).click()
}
test('会话退出等待期间双向锁定策略提交，完成后重新核对', async ({ page }) => {
  await authenticatedSession(page)
  const catalog = policyCatalogFixture()
  let release!: () => void, policyPosts = 0
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: catalog }))
  await page.route('**/api/v27/session/logout', async route => { await waiting; await route.fulfill({ status: 503, json: {} }) })
  await page.route('**/api/v27/policies/activate', route => { policyPosts++; return route.fulfill({ status: 409, json: {} }) })
  await openPolicies(page)
  await page.getByLabel('候选策略版本', { exact: true }).selectOption(catalog.versions[1].version)
  await page.getByLabel('策略切换原因', { exact: true }).fill('核对退出期间的互斥')
  const confirmation = page.getByLabel('我已核对完整配置差异、启用范围与历史快照边界')
  await confirmation.check()
  await page.getByLabel('确认退出并清除当前页面的账号数据').check()
  await page.getByRole('button', { name: '退出本机账号', exact: true }).click()
  await expect(page.getByRole('button', { name: '确认切换策略版本', exact: true })).toBeDisabled()
  await expect(confirmation).not.toBeChecked()
  await expect(page.locator('.policy-versions')).toContainText('会话操作正在等待回执')
  await page.locator('.policy-switch-form').dispatchEvent('submit')
  expect(policyPosts).toBe(0)
  release()
  await expect(page.locator('.session-panel').getByRole('alert')).toContainText('重新读取会话')
  await expect(confirmation).toBeEnabled()
  await expect(confirmation).not.toBeChecked()
})

test('策略差异与只读配置、切换/回滚审计、提交锁定及四档截图', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-18', { recursive: true })
  await authenticatedSession(page)
  let catalog = policyCatalogFixture(), posts = 0, release: () => void = () => {}
  const wait = new Promise<void>(resolve => { release = resolve }), original = JSON.stringify(catalog.versions)
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: catalog }))
  await page.route('**/api/v27/policies/activate', async route => {
    posts++
    const body = route.request().postDataJSON()
    expect(body.expected_revision).toBe(catalog.revision)
    expect(body.expected_active_version).toBe(catalog.active!.version)
    expect(body.expected_active_hash).toBe(catalog.active!.content_hash)
    expect(route.request().headers()['idempotency-key']).toBe(body.request_id)
    expect(route.request().headers()['x-csrf-token']).toBe('test-only-csrf')
    if (posts === 1) await wait
    const targetIndex = catalog.versions.findIndex(item => item.version === body.target_version)
    expect(body.target_hash).toBe(catalog.versions[targetIndex].content_hash)
    const receipt = policyReceiptFixture(catalog, body.request_id, body.reason, targetIndex)
    catalog = receipt.catalog
    return route.fulfill({ json: receipt })
  })
  await openPolicies(page)
  const panel = page.locator('.policy-versions')
  await page.getByLabel('候选策略版本', { exact: true }).selectOption('policy-2.7.2')
  await expect(panel.locator('.policy-diff')).toContainText('FILE_WRITE · Gate')
  await expect(panel.locator('.policy-diff')).toContainText('债务复核阈值')
  await panel.getByText('查看当前与候选的完整只读配置').click()
  await expect(panel.locator('.policy-configuration')).toHaveCount(2)
  await expect(panel.locator('.policy-configuration').first()).toContainText('redaction-1')
  await panel.getByText('查看当前与候选的完整只读配置').click()
  await page.getByLabel('策略切换原因', { exact: true }).fill('核对阻断规则与债务阈值后启用')
  await page.getByLabel('我已核对完整配置差异、启用范围与历史快照边界').check()
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await panel.screenshot({ path: `../docs/reviews/ui/quality-round-18/policy-switch-${width}.png`, animations: 'disabled' })
  }
  const accessibility = await new AxeBuilder({ page }).include('.policy-versions').analyze()
  expect(accessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([])
  await page.getByRole('button', { name: '确认切换策略版本', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在切换策略…' })).toBeDisabled()
  await expect(page.getByLabel('配置来源')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '退出本机账号' })).toBeDisabled()
  await inspectState(page, 'policy-submitting', '.policy-versions', 71)
  release()
  await expect(page.locator('.settings-panel').getByRole('status').filter({ hasText: '已确认切换至' })).toContainText('已确认切换至 policy-2.7.2')
  await expect(page.getByLabel('候选策略版本', { exact: true })).toHaveValue('')
  await inspectState(page, 'policy-activated', '.settings-panel', 74)
  await confirmSwitch(page, 'policy-2.7.1', '回滚至已发布的复核基线')
  await expect(page.locator('.settings-panel').getByRole('status').filter({ hasText: '已确认切换至' })).toContainText('已确认切换至 policy-2.7.1')
  expect(posts).toBe(2)
  await inspectState(page, 'policy-rolled-back', '.settings-panel', 74)
  expect(JSON.stringify(catalog.versions)).toBe(original)
})

test('切换冲突和网络结果未知均锁定，刷新后需重新核对候选与原因', async ({ page }) => {
  await authenticatedSession(page)
  const catalog = policyCatalogFixture()
  let posts = 0, conflict = true
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: catalog }))
  await page.route('**/api/v27/policies/activate', route => { posts++; return conflict ? route.fulfill({ status: 409, json: {} }) : route.abort('failed') })
  await openPolicies(page)
  await confirmSwitch(page, 'policy-2.7.2', '核对后变更')
  const panel = page.locator('.policy-versions')
  await expect(panel.getByRole('alert')).toContainText('已变化')
  await inspectState(page, 'policy-conflict', '.settings-panel', 73)
  await expect(page.getByRole('button', { name: '确认切换策略版本', exact: true })).toBeDisabled()
  expect(posts).toBe(1)
  await panel.getByRole('button', { name: '刷新配置并重新核对' }).click()
  await expect(page.getByLabel('候选策略版本', { exact: true })).toHaveValue('')
  await page.getByLabel('候选策略版本', { exact: true }).selectOption('policy-2.7.2')
  await expect(page.getByLabel('策略切换原因', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('我已核对完整配置差异、启用范围与历史快照边界')).not.toBeChecked()
  conflict = false
  await confirmSwitch(page, 'policy-2.7.2', '重新核对后变更')
  await expect(panel.getByRole('alert')).toContainText('结果未知')
  await inspectState(page, 'policy-unknown', '.settings-panel', 73)
  await expect(page.getByRole('button', { name: '确认切换策略版本', exact: true })).toBeDisabled()
  expect(posts).toBe(2)
})

test('策略切换等待回执时阻止工作台导航与浏览器返回，回执后恢复并保存截图', async ({ page }) => {
  await mkdir('../docs/reviews/ui/quality-round-39', { recursive: true })
  await authenticatedSession(page)
  const catalog = policyCatalogFixture()
  let release: () => void = () => {}
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog) }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: catalog }))
  await page.route('**/api/v27/policies/activate', async route => {
    await waiting
    return route.fulfill({ status: 422, json: {} })
  })
  await openPolicies(page)
  await confirmSwitch(page, 'policy-2.7.2', '等待期间导航保护验收')
  const busyHash = new URL(page.url()).hash
  await expect(page.getByRole('button', { name: '正在切换策略…' })).toBeDisabled()
  await page.locator('.workbench-nav nav button').nth(1).click()
  await expect(page.locator('.demo-notice')).toContainText('管理操作正在等待回执')
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()
  await page.evaluate(() => { history.pushState(null, '', '#section=工作台'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect.poll(() => new URL(page.url()).hash).toBe(busyHash)
  await expect(page.locator('.demo-notice')).toContainText('管理操作正在等待回执')
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.demo-notice').screenshot({ path: `../docs/reviews/ui/quality-round-39/policy-navigation-${width}.png`, animations: 'disabled' })
  }
  release()
  await expect(page.locator('.policy-versions').getByRole('alert')).toContainText('启用条件')
  await expect(page.locator('.workbench-nav nav button').first()).toBeEnabled()
  await page.locator('.workbench-nav nav button').first().click()
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
})

test('只读账号可看差异但不能切换，错误生效指针不展示成功', async ({ page }) => {
  await authenticatedSession(page)
  const catalog = policyCatalogFixture(); catalog.can_switch = false
  let mismatch = false
  await page.route('**/api/v27/settings', route => route.fulfill({ json: policySettingsFixture(catalog, '开发者') }))
  await page.route('**/api/v27/policies', route => route.fulfill({ json: mismatch ? { ...catalog, active: { version: catalog.versions[1].version, content_hash: catalog.versions[1].content_hash } } : catalog }))
  await openPolicies(page)
  await page.getByLabel('候选策略版本', { exact: true }).selectOption('policy-2.7.2')
  await expect(page.locator('.policy-versions')).toContainText('当前账号只能查看版本')
  await expect(page.locator('.policy-versions form')).toHaveCount(0)
  await inspectState(page, 'policy-readonly', '.settings-panel', 73)
  mismatch = true
  await page.getByRole('button', { name: '读取版本目录', exact: true }).click()
  await expect(page.locator('.policy-versions').getByRole('alert')).toContainText('快照不一致')
  await expect(page.locator('.policy-versions .policy-diff')).toHaveCount(0)
  await inspectState(page, 'policy-snapshot-mismatch', '.settings-panel', 73)
})
