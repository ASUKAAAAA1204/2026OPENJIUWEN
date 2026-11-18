import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'

/** 键盘流程：反复 Tab 直到焦点落到文本匹配的元素上（仅用键盘，不点鼠标） */
async function tabUntil(page: Page, match: (text: string) => boolean, max = 40): Promise<boolean> {
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press('Tab')
    const text = await page.evaluate(() => (document.activeElement?.textContent ?? '').trim())
    if (match(text)) return true
  }
  return false
}

test('E1 pre 坐标与五维计数与 mock 一致', async ({ page }) => {
  await page.goto('/?mock=pre')
  const coord = page.locator('.coord')
  await expect(coord).toContainText('assurance-skill@2.2.0')
  await expect(coord).toContainText('deepseek-v4.1-flash')
  await expect(coord).toContainText('retail-ops@2026-10')
  await expect(page.locator('.score-card__num')).toHaveText('47')
  await expect(page.locator('.score-card__band')).toHaveText('D 档')
  await expect(page.locator('.dim__count')).toHaveText(['9/20', '3/10', '8/20', '6/10', '15/20'])
  await expect(page.locator('.dim__weight')).toHaveText(['25%', '25%', '20%', '15%', '15%'])
})

test('E2 点 L 维：抽屉 20 条、11 条未定位、两层内到详情', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.locator('.dim').first().click()

  const drawer = page.locator('.drawer')
  await expect(drawer).toBeVisible()
  await expect(page.locator('.drawer__title')).toHaveText('可定位')
  await expect(page.locator('.drawer__count')).toHaveText('9/20')
  await expect(page.locator('.claim')).toHaveCount(20)
  await expect(page.locator('.claim__status.is-warn')).toHaveCount(11)

  // 第 1 层：结论行；第 2 层：详情
  await page.locator('.claim__head').first().click()
  await expect(page.locator('.claim__head').first()).toHaveAttribute('aria-expanded', 'true')
  const detail = page.locator('.claim__detail.is-open').first()
  await expect(detail).toBeVisible()
  await expect(detail).toContainText('NO_LOCATOR')
  await expect(detail).toContainText('缺失')

  await page.keyboard.press('Escape')
  await expect(drawer).toHaveCount(0)
})

test('E3 发起发布：弹窗、无 X、Esc 不关闭、焦点在主按钮', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.getByRole('button', { name: '发起发布' }).click()

  const gate = page.locator('.gate')
  await expect(gate).toBeVisible()
  await expect(page.locator('.gate__title')).toHaveText('阻断发布')
  await expect(page.locator('.gate__reason-text')).toHaveText('总审计就绪度低于发布门槛')
  await expect(gate.getByRole('button', { name: '关闭' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /执行修复/ })).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(gate).toBeVisible()
})

test('E4 执行修复：切到 79 / B，曲线含 (34, 79)', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.getByRole('button', { name: '发起发布' }).click()
  await page.getByRole('button', { name: /执行修复/ }).click()

  await expect(page.locator('.score-card__num')).toHaveText('79', { timeout: 5000 })
  await expect(page.locator('.score-card__band')).toHaveText('B 档')
  await expect(page.locator('.gate__title')).toHaveText('放行发布')
  await expect(page.locator('.curve__summary')).toContainText('从 47 分到 79 分 · 花费 34 CU')
})

test('E5 豁免：未选原因码不可提交，提交后 banner 常驻', async ({ page }) => {
  await page.goto('/?mock=pre')
  await page.getByRole('button', { name: '发起发布' }).click()
  await page.getByRole('button', { name: /豁免发布/ }).click()

  await expect(page.locator('.gate__title')).toHaveText('豁免发布')
  const submit = page.getByRole('button', { name: '提交豁免' })
  await expect(submit).toBeDisabled()

  await page.locator('.ack__reasons .reason').first().click()
  await page.locator('.ack__input').fill('预算已耗尽，按低风险发布')
  await expect(submit).toBeEnabled()
  await submit.click()

  const banner = page.locator('.ack-bar')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('已豁免 · 原因码')
  await expect(banner).toContainText('BUDGET_EXHAUSTED')
  await expect(page.getByRole('button', { name: '查看豁免记录' })).toBeVisible()
})

test('E6 partial：总分占位，闸门按钮禁用', async ({ page }) => {
  await page.goto('/?mock=partial')
  await expect(page.locator('.score-card__empty')).toBeVisible()
  await expect(page.locator('.score-card__num')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发起发布' })).toBeDisabled()
  await expect(page.locator('.budget__pill')).toHaveCount(0)
})

test('E7 design：只提示不阻断，闸门可记录放行', async ({ page }) => {
  await page.goto('/?mock=design')
  await expect(page.locator('.score-card__empty')).toBeVisible()
  await page.getByRole('button', { name: '发起发布' }).click()
  await expect(page.locator('.gate__title')).toHaveText('设计态放行')
  await expect(page.getByRole('button', { name: '记录设计态放行' })).toBeVisible()
})

test('E8 导出：文件名与内容数字一致', async ({ page }) => {
  await page.goto('/?mock=pre')
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出 JSON' }).click()
  const download = await downloadPromise

  expect(download.suggestedFilename()).toMatch(
    /^ars_assurance-skill-2\.2\.0_deepseek-v4\.1-flash_\d{8}\.json$/,
  )
  const saved = await download.path()
  const payload = JSON.parse(await readFile(saved, 'utf8')) as {
    score: number
    band: string
    dims: unknown[]
    plan: { budget_cu: number; predicted: number }
    gate: { decision: string; threshold: number }
  }
  expect(payload.score).toBe(47)
  expect(payload.band).toBe('D')
  expect(payload.dims).toHaveLength(5)
  expect(payload.plan.budget_cu).toBe(34)
  expect(payload.plan.predicted).toBe(79)
  expect(payload.gate).toMatchObject({ threshold: 70, decision: 'BLOCK' })
})

test('E9 键盘：仅用 Tab/Enter/Esc 完成主流程', async ({ page }) => {
  await page.goto('/?mock=pre')
  expect(await tabUntil(page, (text) => text === '发起发布')).toBe(true)

  await page.keyboard.press('Enter')
  await expect(page.locator('.gate')).toBeVisible()
  await expect(page.getByRole('button', { name: /执行修复/ })).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(page.locator('.gate')).toBeVisible()

  await page.keyboard.press('Enter')
  await expect(page.locator('.score-card__num')).toHaveText('79', { timeout: 5000 })
  await expect(page.getByRole('button', { name: '放行发布' })).toBeFocused()

  await page.keyboard.press('Enter')
  await expect(page.locator('.gate')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发起发布' })).toBeFocused()
  await expect(page.locator('.score-card__num')).toHaveText('79')
})

test('E10 reduced-motion：无位移动画，数字直达', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/?mock=pre')
  await expect(page.locator('.score-card__num')).toHaveText('47')

  const motion = await page.evaluate(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const stage = document.querySelector('.stage-enter')
    const btn = document.querySelector('.btn')
    const ring = document.querySelector('.seal__ring')
    const dur = (el: Element | null) =>
      el ? parseFloat(getComputedStyle(el).animationDuration || '0') : null
    return {
      reduce,
      stageOpacity: stage ? getComputedStyle(stage).opacity : null,
      stageTransform: stage ? getComputedStyle(stage).transform : null,
      btnTransition: btn ? parseFloat(getComputedStyle(btn).transitionDuration || '0') : null,
      ringAnimation: dur(ring),
    }
  })
  expect(motion.reduce).toBe(true)
  expect(motion.stageOpacity).toBe('1')
  expect(motion.stageTransform).toBe('none')
  expect(motion.btnTransition).toBeLessThan(0.01)
  expect(motion.ringAnimation).toBeLessThan(0.01)
})

test('本地字体全部加载（离线演示不依赖 CDN）', async ({ page }) => {
  await page.goto('/?mock=pre')
  const faces = await page.evaluate(async () => {
    await document.fonts.ready
    return Array.from(document.fonts).map((f) => ({
      family: f.family.replace(/"/g, ''),
      weight: f.weight,
      status: f.status,
      used: f.status === 'loaded',
    }))
  })
  const loaded = faces.filter((f) => f.used)
  expect(loaded).toHaveLength(5)
  expect(new Set(loaded.map((f) => `${f.family}:${f.weight}`)).size).toBe(5)
  expect(loaded.map((f) => f.family)).toEqual(
    expect.arrayContaining([
      'Cormorant Garamond',
      'Noto Serif SC',
      'IBM Plex Mono',
      'IBM Plex Sans',
    ]),
  )
})
