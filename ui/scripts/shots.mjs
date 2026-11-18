/**
 * 组件截图（开发计划书 §6.1）。
 * 用法：npm run shots
 * 输出：docs/reviews/ui/<组件名>/v1-<状态>.png
 */
import { chromium } from '@playwright/test'
import { createServer } from 'vite'
import { mkdir, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const UI_ROOT = path.resolve(HERE, '..')
const OUT_ROOT = path.resolve(UI_ROOT, '..', 'docs', 'reviews', 'ui')
const PORT = 5199
let BASE = `http://127.0.0.1:${PORT}`

const SHOTS = [
  // ScoreCard
  { comp: 'ScoreCard', file: 'v1-01-normal', url: '/?mock=pre', sel: '.score-card' },
  { comp: 'ScoreCard', file: 'v1-02-no-score', url: '/?mock=partial', sel: '.score-card' },
  { comp: 'ScoreCard', file: 'v1-03-design-only', url: '/?mock=design', sel: '.score-card' },
  { comp: 'ScoreCard', file: 'v1-04-edge', url: '/?mock=edge', sel: '.score-card' },
  { comp: 'ScoreCard', file: 'v1-05-focus', url: '/?mock=pre', sel: null, focus: '.coord .btn' },
  { comp: 'ScoreCard', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: null, viewport: [1024, 768] },
  { comp: 'ScoreCard', file: 'v1-07-hover', url: '/?mock=pre', sel: null, hover: '.coord .btn' },

  // CoordBar
  { comp: 'CoordBar', file: 'v1-01-normal', url: '/?mock=pre', sel: '.coord' },
  { comp: 'CoordBar', file: 'v1-02-missing-segment', url: '/?mock=edge', sel: '.coord' },
  { comp: 'CoordBar', file: 'v1-03-partial', url: '/?mock=partial', sel: '.coord' },
  { comp: 'CoordBar', file: 'v1-04-design-only', url: '/?mock=design', sel: '.coord' },
  { comp: 'CoordBar', file: 'v1-05-focus', url: '/?mock=pre', sel: '.coord', focus: '.coord .btn' },
  { comp: 'CoordBar', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: null, viewport: [1024, 768] },
  { comp: 'CoordBar', file: 'v1-07-hover', url: '/?mock=pre', sel: '.coord', hover: '.coord .btn' },

  // StatusBadge
  { comp: 'StatusBadge', file: 'v1-01-complete', url: '/?mock=pre', sel: '.badge' },
  { comp: 'StatusBadge', file: 'v1-02-partial', url: '/?mock=partial', sel: '.badge' },
  { comp: 'StatusBadge', file: 'v1-03-design-only', url: '/?mock=design', sel: '.badge' },
  { comp: 'StatusBadge', file: 'v1-04-in-context', url: '/?mock=edge', sel: '.coord' },
  { comp: 'StatusBadge', file: 'v1-05-focus', url: '/?mock=pre', sel: null, focus: '.coord .btn' },
  { comp: 'StatusBadge', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: null, viewport: [1024, 768] },

  // DimensionBars
  { comp: 'DimensionBars', file: 'v1-01-normal', url: '/?mock=pre', sel: '.dims' },
  { comp: 'DimensionBars', file: 'v1-02-na-missing', url: '/?mock=design', sel: '.dims' },
  { comp: 'DimensionBars', file: 'v1-03-insufficient', url: '/?mock=partial', sel: '.dims' },
  { comp: 'DimensionBars', file: 'v1-04-edge', url: '/?mock=edge', sel: '.dims' },
  { comp: 'DimensionBars', file: 'v1-05-focus', url: '/?mock=pre', sel: null, tabs: 2 },
  { comp: 'DimensionBars', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: '.dims', viewport: [1024, 768] },
  { comp: 'DimensionBars', file: 'v1-07-hover', url: '/?mock=pre', sel: '.dims', hover: '.dim' },

  // DeductionList
  { comp: 'DeductionList', file: 'v1-01-normal', url: '/?mock=pre', sel: '.ded' },
  { comp: 'DeductionList', file: 'v1-02-empty', url: '/?mock=partial', sel: '.ded' },
  { comp: 'DeductionList', file: 'v1-03-edge', url: '/?mock=edge', sel: '.ded' },
  { comp: 'DeductionList', file: 'v1-04-focus', url: '/?mock=pre', sel: '.ded', tabs: 7 },
  { comp: 'DeductionList', file: 'v1-05-viewport-1024', url: '/?mock=pre', sel: '.ded', viewport: [1024, 768] },
  { comp: 'DeductionList', file: 'v1-06-hover', url: '/?mock=pre', sel: '.ded', hover: '.ded__row' },

  // PlanTable
  { comp: 'PlanTable', file: 'v1-01-normal', url: '/?mock=pre', sel: '.plan' },
  { comp: 'PlanTable', file: 'v1-02-empty', url: '/?mock=partial', sel: '.plan' },
  { comp: 'PlanTable', file: 'v1-03-design', url: '/?mock=design', sel: '.plan' },
  { comp: 'PlanTable', file: 'v1-04-edge', url: '/?mock=edge', sel: '.plan' },
  { comp: 'PlanTable', file: 'v1-05-post', url: '/?mock=post', sel: '.plan' },
  { comp: 'PlanTable', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: '.plan', viewport: [1024, 768] },
  { comp: 'PlanTable', file: 'v1-07-in-context', url: '/?mock=pre', sel: null },

  // BudgetPills
  { comp: 'BudgetPills', file: 'v1-01-normal', url: '/?mock=pre', sel: '.budget' },
  { comp: 'BudgetPills', file: 'v1-02-zero-tier', url: '/?mock=pre', sel: '.budget', click: '.budget__pill' },
  { comp: 'BudgetPills', file: 'v1-03-mid-tier', url: '/?mock=pre', sel: '.budget', click: '.budget__pill:nth-of-type(3)' },
  { comp: 'BudgetPills', file: 'v1-04-empty', url: '/?mock=partial', sel: '.budget' },
  { comp: 'BudgetPills', file: 'v1-05-focus', url: '/?mock=pre', sel: '.budget', tabs: 10 },
  { comp: 'BudgetPills', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: '.budget', viewport: [1024, 768] },
  { comp: 'BudgetPills', file: 'v1-07-in-context', url: '/?mock=pre', sel: null },

  // CostCurve
  { comp: 'CostCurve', file: 'v1-01-normal', url: '/?mock=pre', sel: '.curve' },
  { comp: 'CostCurve', file: 'v1-02-post', url: '/?mock=post', sel: '.curve' },
  { comp: 'CostCurve', file: 'v1-03-hover', url: '/?mock=pre', sel: '.curve', hover: '.curve__plot' },
  { comp: 'CostCurve', file: 'v1-04-empty', url: '/?mock=partial', sel: '.curve' },
  { comp: 'CostCurve', file: 'v1-05-design-empty', url: '/?mock=design', sel: '.curve' },
  { comp: 'CostCurve', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: '.curve', viewport: [1024, 768] },
  { comp: 'CostCurve', file: 'v1-07-in-context', url: '/?mock=pre', sel: null },

  // EvidenceDrawer
  { comp: 'EvidenceDrawer', file: 'v1-01-open', url: '/?mock=pre&drawer=L', sel: null },
  { comp: 'EvidenceDrawer', file: 'v1-02-detail', url: '/?mock=pre&drawer=L', sel: null, click: '.claim__head' },
  { comp: 'EvidenceDrawer', file: 'v1-03-empty', url: '/?mock=pre&drawer=D', sel: null },
  { comp: 'EvidenceDrawer', file: 'v1-04-missing', url: '/?mock=design&drawer=R', sel: null },
  { comp: 'EvidenceDrawer', file: 'v1-05-viewport-1024', url: '/?mock=pre&drawer=L', sel: null, viewport: [1024, 768] },
  { comp: 'EvidenceDrawer', file: 'v1-06-focus', url: '/?mock=pre&drawer=L', sel: '.drawer__panel', tabs: 2 },

  // GateDialog
  { comp: 'GateDialog', file: 'v1-01-block', url: '/?mock=pre&gate=1', sel: null },
  { comp: 'GateDialog', file: 'v1-02-pass', url: '/?mock=post&gate=1', sel: null },
  { comp: 'GateDialog', file: 'v1-03-design', url: '/?mock=design&gate=1', sel: null },
  { comp: 'GateDialog', file: 'v1-04-sample', url: '/?mock=partial&gate=1', sel: null },
  { comp: 'GateDialog', file: 'v1-05-edge', url: '/?mock=edge&gate=1', sel: null },
  { comp: 'GateDialog', file: 'v1-06-focus', url: '/?mock=pre&gate=1', sel: null, tabs: 1 },
  { comp: 'GateDialog', file: 'v1-07-viewport-1024', url: '/?mock=pre&gate=1', sel: null, viewport: [1024, 768] },

  // AckForm（弹窗内第 2 步）
  { comp: 'AckForm', file: 'v1-01-empty', url: '/?mock=pre&gate=1', sel: null, click: '.gate__btn--ack' },
  {
    comp: 'AckForm',
    file: 'v1-02-filled',
    url: '/?mock=pre&gate=1',
    sel: null,
    click: '.gate__btn--ack',
    steps: [
      { click: '.ack__reasons .reason' },
      { fill: { sel: '.ack__input', value: '预算已耗尽，无法继续补定位，按低风险发布' } },
    ],
  },
  {
    comp: 'AckForm',
    file: 'v1-03-need-note',
    url: '/?mock=pre&gate=1',
    sel: null,
    click: '.gate__btn--ack',
    steps: [{ click: '.ack__reasons .reason' }],
  },
  {
    comp: 'AckForm',
    file: 'v1-04-submitted',
    url: '/?mock=pre&gate=1',
    sel: null,
    click: '.gate__btn--ack',
    steps: [
      { click: '.ack__reasons .reason' },
      { fill: { sel: '.ack__input', value: '预算已耗尽，无法继续补定位，按低风险发布' } },
      { click: '.ack__actions .btn--primary' },
    ],
  },
  {
    comp: 'AckForm',
    file: 'v1-05-record',
    url: '/?mock=pre&gate=1',
    sel: null,
    click: '.gate__btn--ack',
    steps: [
      { click: '.ack__reasons .reason' },
      { fill: { sel: '.ack__input', value: '预算已耗尽，无法继续补定位，按低风险发布' } },
      { click: '.ack__actions .btn--primary' },
      { click: '.opsbar .btn' },
    ],
  },
  { comp: 'AckForm', file: 'v1-06-focus', url: '/?mock=pre&gate=1', sel: null, click: '.gate__btn--ack', tabs: 1 },
  {
    comp: 'AckForm',
    file: 'v1-07-viewport-1024',
    url: '/?mock=pre&gate=1',
    sel: null,
    click: '.gate__btn--ack',
    viewport: [1024, 768],
  },

  // Studio 一屏联调（W0-C5）：1280×720 检查操作条是否在折叠线以上
  { comp: 'Studio', file: 'v1-01-pre-1280', url: '/?mock=pre', sel: null },
  { comp: 'Studio', file: 'v1-02-post-1280', url: '/?mock=post', sel: null },
  { comp: 'Studio', file: 'v1-03-partial-1280', url: '/?mock=partial', sel: null },
  { comp: 'Studio', file: 'v1-04-design-1280', url: '/?mock=design', sel: null },
  { comp: 'Studio', file: 'v1-05-edge-1280', url: '/?mock=edge', sel: null },
  { comp: 'Studio', file: 'v1-06-viewport-1024', url: '/?mock=pre', sel: null, viewport: [1024, 768] },
  { comp: 'Studio', file: 'v1-07-pre-gate', url: '/?mock=pre&gate=1', sel: null },
  {
    comp: 'Studio',
    file: 'v1-08-ack-banner',
    url: '/?mock=pre&gate=1',
    sel: null,
    steps: [
      { click: '.gate__btn--ack' },
      { click: '.ack__reasons .reason' },
      { fill: { sel: '.ack__input', value: '预算已耗尽，无法继续补定位，按低风险发布' } },
      { click: '.ack__actions .btn--primary' },
    ],
  },
]

async function waitForServer(timeoutMs = 20000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(BASE)
      if (res.ok) return
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  // fetch 可能被环境拦截；vite 已 listen 时直接继续，交给 Playwright 访问
  console.warn(`探测 ${BASE} 超时，仍尝试直接打开页面`)
}

const server = await createServer({
  root: UI_ROOT,
  logLevel: 'error',
  server: { port: PORT, strictPort: true, host: '127.0.0.1' },
})
await server.listen()
BASE = server.resolvedUrls?.local?.[0] ?? BASE
const browser = await chromium.launch()
const errors = []

try {
  await waitForServer()
  await rm(OUT_ROOT, { recursive: true, force: true })

  for (const shot of SHOTS) {
    const [w, h] = shot.viewport ?? [1280, 720]
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 })
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(`${shot.comp}/${shot.file}: ${msg.text()}`)
    })
    page.on('pageerror', (err) => errors.push(`${shot.comp}/${shot.file}: ${err.message}`))

    await page.goto(BASE + shot.url, { waitUntil: 'networkidle' })
    await page.waitForSelector('.coord', { timeout: 10000 })
    await page.evaluate(() => document.fonts.ready)
    await page.waitForTimeout(1100)

    if (shot.click) {
      await page.click(shot.click)
      await page.waitForTimeout(500)
    }
    for (const step of shot.steps ?? []) {
      if (step.click) await page.click(step.click)
      else if (step.fill) await page.fill(step.fill.sel, step.fill.value)
      else if (step.press) await page.keyboard.press(step.press)
      await page.waitForTimeout(350)
    }
    if (shot.tabs) {
      for (let i = 0; i < shot.tabs; i += 1) await page.keyboard.press('Tab')
    }
    if (shot.hover) await page.hover(shot.hover)
    if (shot.focus) await page.keyboard.press('Tab')
    await page.waitForTimeout(200)

    const dir = path.join(OUT_ROOT, shot.comp)
    await mkdir(dir, { recursive: true })
    const file = path.join(dir, `${shot.file}.png`)

    if (shot.sel) {
      const el = page.locator(shot.sel)
      await el.scrollIntoViewIfNeeded()
      await page.waitForTimeout(150)
      const box = await el.boundingBox()
      if (!box) throw new Error(`${shot.sel} 无可截图区域`)
      const pad = 16
      const x = Math.max(0, box.x - pad)
      const y = Math.max(0, box.y - pad)
      const clip = {
        x,
        y,
        width: Math.min(w - x, box.width + pad * 2),
        height: Math.min(h - y, box.height + pad * 2),
      }
      await page.screenshot({ path: file, clip })
    } else {
      await page.screenshot({ path: file, fullPage: false })
    }
    await page.close()
    console.log(`✓ ${shot.comp}/${shot.file}.png`)
  }
} finally {
  await browser.close()
  await server.close()
  if (errors.length) {
    console.error('\n控制台错误：')
    errors.forEach((e) => console.error(' - ' + e))
    process.exitCode = 1
  } else {
    console.log('\n控制台错误：0')
  }
}
