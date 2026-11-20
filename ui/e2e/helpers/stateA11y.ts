import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'

export async function inspectState(page: Page, name: string, selector: string, round = 66) {
  const originalViewport = page.viewportSize()
  const directory = `../docs/reviews/ui/quality-round-${round}`
  await mkdir(directory, { recursive: true })
  const results = []
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.evaluate(() => document.fonts.ready)
    const scan = await new AxeBuilder({ page }).include('.workbench-shell').analyze()
    results.push({ width, violations: scan.violations.map(value => ({ id: value.id, impact: value.impact, targets: value.nodes.map(node => node.target) })) })
    expect(scan.violations.filter(value => ['serious', 'critical'].includes(value.impact ?? '')), `${name} ${width}`).toEqual([])
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator(selector).screenshot({ path: `${directory}/${name}-${width}.png`, animations: 'disabled' })
  }
  await writeFile(`${directory}/${name}-a11y.json`, JSON.stringify(results, null, 2))
  if (originalViewport) await page.setViewportSize(originalViewport)
}
