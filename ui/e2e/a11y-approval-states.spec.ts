import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { createApprovalDemo } from '../src/components/workbench/approvals'
import { authenticatedSession } from './helpers/authenticatedSession'

test('审批可提交、提交中、结果未知、只读与到期状态四档可访问', async ({ page }) => {
  test.setTimeout(120000)
  await authenticatedSession(page)
  const item = createApprovalDemo(Date.now() + 600000)[0]!
  let release = () => {}, posts = 0
  const waiting = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v27/approvals', route => route.fulfill({ json: [item] }))
  await page.route('**/api/v27/approvals/*/respond', async route => { posts++; await waiting; await route.abort('failed') })
  const directory = '../docs/reviews/ui/quality-round-65'
  await mkdir(directory, { recursive: true })
  const results: unknown[] = []
  async function inspect(state: string) {
    for (const width of [1440, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.evaluate(() => document.fonts.ready)
      const scan = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
      results.push({ state, width, violations: scan.violations.map(value => ({ id: value.id, impact: value.impact, targets: value.nodes.map(node => node.target) })) })
      expect(scan.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? '')), `${state} ${width}`).toEqual([])
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.locator('.approval-workspace').screenshot({ path: `${directory}/${state}-${width}.png`, animations: 'disabled' })
    }
  }
  await page.goto('/?approval=api#section=人工审批')
  await expect(page.getByLabel('审批说明')).toBeEnabled()
  await inspect('ready')
  await page.getByLabel('我已核对目标、参数和操作范围').check()
  await page.getByRole('button', { name: '批准请求', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在提交…' })).toBeDisabled()
  await inspect('submitting')
  release()
  await expect(page.getByRole('alert')).toContainText('提交结果未知')
  await expect(page.getByRole('button', { name: '批准请求', exact: true })).toBeDisabled()
  await inspect('unknown')
  expect(posts).toBe(1)
  item.can_respond = false
  await page.getByRole('button', { name: '刷新并重新核对' }).click()
  await expect(page.getByLabel('审批说明')).toBeDisabled()
  await expect(page.getByLabel('我已核对目标、参数和操作范围')).not.toBeChecked()
  await inspect('read-only')
  item.expires_at = new Date(Date.now() - 1000).toISOString()
  item.requested_at = new Date(Date.now() - 60000).toISOString()
  await page.getByRole('button', { name: '全部', exact: true }).click()
  await page.getByRole('button', { name: '刷新队列', exact: true }).click()
  await expect(page.getByRole('heading', { name: '审批窗口已结束' })).toBeVisible()
  await expect(page.getByRole('button', { name: '批准请求', exact: true })).toHaveCount(0)
  await inspect('expired')
  await writeFile(`${directory}/approval-a11y.json`, JSON.stringify(results, null, 2))
})
