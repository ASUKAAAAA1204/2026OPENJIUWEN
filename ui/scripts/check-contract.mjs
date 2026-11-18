/**
 * 契约校验：用 src/api/types.ts 的口径核对五套 mock。
 * 不计算分数/增益，只检查字段存在性、类型、枚举与结构一致性。
 * 用法：npm run check:contract
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const UI_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const MOCK_DIR = path.join(UI_ROOT, 'src', 'mock')
const SCENARIOS = ['pre', 'post', 'partial', 'design', 'edge']

const DIM_KEYS = ['L', 'C', 'R', 'D', 'F']
const DOC_STATES = ['DESIGN_ONLY', 'PARTIAL', 'COMPLETE']
const BANDS = ['A', 'B', 'C', 'D']
const DIM_STATES = ['VALUE', 'NA', 'MISSING']
const DECISIONS = ['BLOCK', 'PASS']
const FORBIDDEN_KEY = /(confidence|accuracy|probability|置信|正确率)/i

const errors = []

function fail(file, where, message) {
  errors.push(`${file} ${where}: ${message}`)
}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNum(value) {
  return typeof value === 'number' && Number.isFinite(value)
}

function isInt(value) {
  return Number.isInteger(value)
}

function requireString(file, where, value, { allowEmpty = false } = {}) {
  if (typeof value !== 'string') fail(file, where, `应为字符串，实际 ${typeof value}`)
  else if (!allowEmpty && value.trim() === '') fail(file, where, '不应为空字符串')
}

function requireNumber(file, where, value) {
  if (!isNum(value)) fail(file, where, `应为数字，实际 ${JSON.stringify(value)}`)
}

function requireInt(file, where, value, min = 0) {
  if (!isInt(value) || value < min) fail(file, where, `应为 ≥${min} 的整数，实际 ${JSON.stringify(value)}`)
}

function requireEnum(file, where, value, allowed) {
  if (!allowed.includes(value)) fail(file, where, `应为 ${allowed.join(' | ')}，实际 ${JSON.stringify(value)}`)
}

function requireNullableNumber(file, where, value) {
  if (value !== null) requireNumber(file, where, value)
}

function requireArray(file, where, value) {
  if (!Array.isArray(value)) fail(file, where, `应为数组，实际 ${typeof value}`)
  return Array.isArray(value) ? value : []
}

function scanForbiddenKeys(file, node, where = '') {
  if (Array.isArray(node)) {
    node.forEach((item, i) => scanForbiddenKeys(file, item, `${where}[${i}]`))
    return
  }
  if (!isPlainObject(node)) return
  for (const [key, value] of Object.entries(node)) {
    if (FORBIDDEN_KEY.test(key)) fail(file, where || '$', `出现禁用字段 "${key}"（不出现置信度/正确率）`)
    scanForbiddenKeys(file, value, `${where}.${key}`)
  }
}

function checkCoord(file, coord) {
  if (!isPlainObject(coord)) return fail(file, 'coord', '应为对象')
  requireString(file, 'coord.skill_version', coord.skill_version)
  requireString(file, 'coord.model', coord.model, { allowEmpty: true })
  requireString(file, 'coord.data_profile', coord.data_profile)
  if (coord.data_digest !== undefined) requireString(file, 'coord.data_digest', coord.data_digest)
}

function checkDims(file, dims) {
  const rows = requireArray(file, 'dims', dims)
  if (rows.length !== 5) fail(file, 'dims', `应恰好 5 条，实际 ${rows.length}`)

  let weightSum = 0
  rows.forEach((dim, i) => {
    const at = `dims[${i}]`
    if (!isPlainObject(dim)) return fail(file, at, '应为对象')
    requireEnum(file, `${at}.key`, dim.key, DIM_KEYS)
    if (dim.key !== DIM_KEYS[i]) fail(file, `${at}.key`, `顺序应为 ${DIM_KEYS.join(',')}`)
    requireString(file, `${at}.label`, dim.label)
    requireNumber(file, `${at}.weight`, dim.weight)
    weightSum += isNum(dim.weight) ? dim.weight : 0
    requireInt(file, `${at}.num`, dim.num)
    requireInt(file, `${at}.den`, dim.den)
    requireEnum(file, `${at}.state`, dim.state, DIM_STATES)

    if (dim.state === 'VALUE' && !(dim.den > 0)) fail(file, at, 'VALUE 态分母应 > 0')
    if (dim.state === 'NA' && dim.den !== 0) fail(file, at, 'NA 态分母应为 0')
    if (dim.state === 'MISSING' && dim.num !== 0) fail(file, at, 'MISSING 态分子应为 0')
    if (isNum(dim.num) && isNum(dim.den) && dim.num > dim.den) fail(file, at, '分子不应大于分母')
  })

  if (Math.abs(weightSum - 100) > 1e-6) fail(file, 'dims', `权重合计应为 100，实际 ${weightSum}`)
}

function checkDeductions(file, rows) {
  requireArray(file, 'deductions', rows).forEach((row, i) => {
    const at = `deductions[${i}]`
    if (!isPlainObject(row)) return fail(file, at, '应为对象')
    requireEnum(file, `${at}.dim`, row.dim, DIM_KEYS)
    requireString(file, `${at}.text`, row.text)
    requireString(file, `${at}.action`, row.action)
    requireInt(file, `${at}.n`, row.n)
    requireNumber(file, `${at}.price_cu`, row.price_cu)
  })
}

function checkPlan(file, plan) {
  if (!isPlainObject(plan)) return fail(file, 'plan', '应为对象')
  requireNumber(file, 'plan.current', plan.current)
  requireEnum(file, 'plan.target_band', plan.target_band, BANDS)
  requireNumber(file, 'plan.total_cu', plan.total_cu)
  requireNumber(file, 'plan.budget_cu', plan.budget_cu)
  requireNumber(file, 'plan.cu_buffer_pct', plan.cu_buffer_pct)
  requireNullableNumber(file, 'plan.predicted', plan.predicted)

  requireArray(file, 'plan.actions', plan.actions).forEach((action, i) => {
    const at = `plan.actions[${i}]`
    if (!isPlainObject(action)) return fail(file, at, '应为对象')
    requireString(file, `${at}.act`, action.act)
    requireString(file, `${at}.label`, action.label)
    requireInt(file, `${at}.n`, action.n)
    requireNumber(file, `${at}.cu`, action.cu)
    requireNumber(file, `${at}.gain`, action.gain)
  })

  requireArray(file, 'plan.tiers', plan.tiers).forEach((tier, i) => {
    const at = `plan.tiers[${i}]`
    if (!isPlainObject(tier)) return fail(file, at, '应为对象')
    requireNumber(file, `${at}.cu`, tier.cu)
    requireNullableNumber(file, `${at}.predicted`, tier.predicted)
    if (i > 0) {
      const prev = plan.tiers[i - 1]
      if (isNum(prev?.cu) && isNum(tier.cu) && tier.cu <= prev.cu) {
        fail(file, at, '档位 CU 应严格递增')
      }
    }
  })
}

function checkTrend(file, trend) {
  const rows = requireArray(file, 'trend', trend)
  rows.forEach((point, i) => {
    const at = `trend[${i}]`
    if (!isPlainObject(point)) return fail(file, at, '应为对象')
    requireNumber(file, `${at}.cu`, point.cu)
    requireInt(file, `${at}.score`, point.score)
    if (i > 0) {
      const prev = rows[i - 1]
      if (isNum(prev?.cu) && isNum(point.cu) && point.cu <= prev.cu) {
        fail(file, at, '曲线 CU 应严格递增')
      }
    }
  })
}

function checkGate(file, gate) {
  if (!isPlainObject(gate)) return fail(file, 'gate', '应为对象')
  requireInt(file, 'gate.threshold', gate.threshold)
  requireEnum(file, 'gate.decision', gate.decision, DECISIONS)
  if (gate.reason_code !== undefined) requireString(file, 'gate.reason_code', gate.reason_code)
}

function checkReport(file, report) {
  if (!isPlainObject(report)) return fail(file, '$', '顶层应为对象')
  scanForbiddenKeys(file, report)

  checkCoord(file, report.coord)
  requireEnum(file, 'doc_state', report.doc_state, DOC_STATES)

  if (report.score !== null) {
    requireInt(file, 'score', report.score, 0)
    if (report.score > 100) fail(file, 'score', `应 ≤ 100，实际 ${report.score}`)
  }
  if (report.band !== null) requireEnum(file, 'band', report.band, BANDS)
  if (report.doc_state !== 'COMPLETE') {
    if (report.score !== null) fail(file, 'score', `${report.doc_state} 不应出总分`)
    if (report.band !== null) fail(file, 'band', `${report.doc_state} 不应出档位`)
  } else {
    if (report.score === null) fail(file, 'score', 'COMPLETE 应出总分')
    if (report.band === null) fail(file, 'band', 'COMPLETE 应出档位')
  }

  if (!isPlainObject(report.counts)) fail(file, 'counts', '应为对象')
  else {
    requireInt(file, 'counts.claims', report.counts.claims)
    requireInt(file, 'counts.rules', report.counts.rules)
    requireInt(file, 'counts.checks', report.counts.checks)
    if (report.counts.rules_compilable !== undefined) {
      requireInt(file, 'counts.rules_compilable', report.counts.rules_compilable)
    }
  }

  checkDims(file, report.dims)
  checkDeductions(file, report.deductions)
  checkPlan(file, report.plan)
  checkTrend(file, report.trend)

  requireNullableNumber(file, 'marginal_exhausted_from', report.marginal_exhausted_from)
  checkGate(file, report.gate)

  if (!Array.isArray(report.warnings)) fail(file, 'warnings', '应为数组')
  else report.warnings.forEach((w, i) => requireString(file, `warnings[${i}]`, w))
}

for (const scenario of SCENARIOS) {
  const file = `${scenario}.json`
  let report
  try {
    report = JSON.parse(readFileSync(path.join(MOCK_DIR, file), 'utf8'))
  } catch (e) {
    fail(file, '$', `无法解析：${e.message}`)
    continue
  }
  checkReport(file, report)
}

if (errors.length) {
  console.error(`契约校验失败（${errors.length}）`)
  errors.forEach((e) => console.error(' - ' + e))
  process.exit(1)
}

console.log(`契约校验通过：${SCENARIOS.length} 套 mock 均符合 types.ts`)
