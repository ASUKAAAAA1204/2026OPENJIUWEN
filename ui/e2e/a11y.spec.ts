import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']

function scan(page: Page) {
  return new AxeBuilder({ page }).withTags(TAGS).analyze()
}

async function expectNoViolation(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready
    // Scan the completed entrance state instead of a random translucent animation frame.
    await Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})))
  })
  const results = await scan(page)
  const violations = results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    targets: v.nodes.map((n) => n.target.join(' ')),
  }))
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([])
}

test('可访问性 1/3：pre 一屏无 WCAG A/AA 违规', async ({ page }) => {
  await page.goto('/?mock=pre')
  await expect(page.locator('.score-card__num')).toHaveText('47')
  await page.evaluate(() => document.fonts.ready)
  await expectNoViolation(page)
})

test('可访问性 2/3：阻断弹窗无 WCAG A/AA 违规', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.getByRole('button', { name: '发起发布' }).click()
  await expect(page.locator('.gate__panel')).toBeVisible()
  await expectNoViolation(page)
})

test('可访问性 3/3：证据抽屉无 WCAG A/AA 违规', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.locator('.dim').first().click()
  await expect(page.locator('.drawer__panel')).toBeVisible()
  await expectNoViolation(page)
})
