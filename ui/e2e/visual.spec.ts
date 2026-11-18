import { expect, test, type Page } from '@playwright/test'

/** 等待字体与首屏编排稳定，避免基线受加载时序影响 */
async function settle(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(500)
}

test('视觉基线 1/6：pre 一屏', async ({ page }) => {
  await page.goto('/?mock=pre')
  await expect(page.locator('.score-card__num')).toHaveText('47')
  await settle(page)
  await expect(page).toHaveScreenshot('pre-viewport.png')
})

test('视觉基线 2/6：pre 抽屉打开', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.locator('.dim').first().click()
  await expect(page.locator('.drawer')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('pre-drawer.png')
})

test('视觉基线 3/6：pre 阻断弹窗', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.getByRole('button', { name: '发起发布' }).click()
  await expect(page.locator('.gate')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('pre-gate.png')
})

test('视觉基线 4/6：post 一屏', async ({ page }) => {
  await page.goto('/?mock=post')
  await expect(page.locator('.score-card__num')).toHaveText('79')
  await settle(page)
  await expect(page).toHaveScreenshot('post-viewport.png')
})

test('视觉基线 5/6：partial 一屏', async ({ page }) => {
  await page.goto('/?mock=partial')
  await expect(page.locator('.score-card__empty')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('partial-viewport.png')
})

test('视觉基线 6/6：design 一屏', async ({ page }) => {
  await page.goto('/?mock=design')
  await expect(page.locator('.score-card__empty')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('design-viewport.png')
})
