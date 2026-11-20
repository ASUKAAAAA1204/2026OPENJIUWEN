import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'

for (const width of [1440, 320]) for (const reduced of [false, true]) test(`${width}px ${reduced ? '减少' : '正常'}动态效果下反馈及时、可读且可关闭`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.emulateMedia({ reducedMotion: reduced ? 'reduce' : 'no-preference' })
  await page.route('**/api/v27/session', route => route.fulfill({ json: { authenticated: false, csrf_token: 'synthetic-csrf', identity: null } }))
  const samples = []
  for (const section of ['工作台', '运行记录', '人工审批', '证据与债务', '版本评估', '设置']) {
    await page.goto('about:blank')
    await page.goto(`/#${new URLSearchParams({ section })}`)
    await expect(page.getByRole('heading', { name: section, exact: true })).toBeVisible()
    const styles = await page.locator('.workbench-shell').evaluate(shell => {
      const milliseconds = (value: string) => value.split(',').map(item => parseFloat(item) * (item.trim().endsWith('ms') ? 1 : 1000))
      return [...shell.querySelectorAll<HTMLElement>('*')].filter(node => node.getClientRects().length > 0).map(node => {
        const style = getComputedStyle(node)
        return { node: `${node.tagName}.${node.className}`, durations: [...milliseconds(style.animationDuration), ...milliseconds(style.transitionDuration)], infinite: style.animationIterationCount.split(',').some(value => value.trim() === 'infinite'), animation: style.animationName }
      })
    })
    expect(styles.flatMap(item => item.durations).every(duration => duration <= (reduced ? 0.01 : 240)), section).toBe(true)
    expect(styles.filter(item => item.infinite), `${section} has no decorative looping animation`).toEqual([])
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    samples.push({ section, maxDurationMs: Math.max(0, ...styles.flatMap(item => item.durations)), infiniteAnimations: 0 })
  }
  await page.getByRole('navigation').getByRole('button', { name: /工作台/ }).click()
  await page.getByLabel('任务描述', { exact: true }).fill('动态效果与反馈合成验收')
  const button = page.getByRole('button', { name: /创建演示任务/ })
  await button.focus()
  await page.keyboard.down('Space')
  if (reduced) await expect(button).toHaveCSS('transform', 'none')
  else await expect(button).not.toHaveCSS('transform', 'none')
  await page.keyboard.up('Space')
  const notice = page.locator('.demo-notice')
  await expect(notice.getByRole('button', { name: '关闭提示' })).toBeVisible()
  await expect(notice).toHaveAttribute('role', 'status')
  await expect(notice).toContainText('未调用模型或执行工具')
  await expect.poll(() => notice.evaluate(node => getComputedStyle(node).opacity)).toBe('1')
  await page.getByRole('button', { name: '关闭提示' }).click()
  await expect(notice).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
  await mkdir('../docs/reviews/ui/quality-round-74', { recursive: true })
  await writeFile(`../docs/reviews/ui/quality-round-74/motion-${width}-${reduced ? 'reduced' : 'normal'}.json`, JSON.stringify({ width, reduced, samples, pressFeedback: reduced ? 'none' : 'transform', notification: 'status, readable, dismissible' }, null, 2))
})
