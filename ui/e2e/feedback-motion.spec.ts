import { workbenchApi } from './helpers/workbenchApi'
import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'

test.beforeEach(async ({ page }) => workbenchApi(page))

for (const width of [1440, 320]) for (const reduced of [false, true]) test(`${width}px ${reduced ? '减少' : '正常'}动态效果下创建反馈及时且可读`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.emulateMedia({ reducedMotion: reduced ? 'reduce' : 'no-preference' })
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
  await page.getByLabel('业务任务标识', { exact: true }).fill('BUSINESS-42')
  await page.getByLabel('任务描述', { exact: true }).fill('核查项目文件并生成修改建议')
  await page.getByLabel('授权工作目录', { exact: true }).selectOption('workspace-1')
  await page.getByLabel('Skill 与版本', { exact: true }).selectOption({ index: 1 })
  await page.getByLabel('我已核对任务标识、授权目录、框架和 Skill 版本').check()
  const button = page.getByRole('button', { name: '创建账本运行', exact: true })
  await button.focus()
  await page.keyboard.down('Space')
  if (reduced) await expect(button).toHaveCSS('transform', 'none')
  else await expect(button).not.toHaveCSS('transform', 'none')
  await page.keyboard.up('Space')
  await expect(page.locator('.run-create-panel').getByRole('status')).toContainText('运行已登记')
  await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
  await mkdir('../docs/reviews/ui/quality-round-74', { recursive: true })
  await writeFile(`../docs/reviews/ui/quality-round-74/motion-${width}-${reduced ? 'reduced' : 'normal'}.json`, JSON.stringify({ width, reduced, samples, pressFeedback: reduced ? 'none' : 'transform', notification: 'status, readable, bound to API receipt' }, null, 2))
})
