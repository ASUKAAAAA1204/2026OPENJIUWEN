import type { Band, DocState } from '../api/types'

/** 整数分；null 显示占位横线，不出现 NaN / undefined */
export function fmtScore(score: number | null): string {
  if (score === null || !Number.isFinite(score)) return '—'
  return String(Math.round(score))
}

/** CU：一位小数，整数省略 .0（34 而非 34.0 / 34.00） */
export function fmtCu(cu: number): string {
  if (!Number.isFinite(cu)) return '—'
  const rounded = Math.round(cu * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

/** 增益：整数分，正数带 + 号（§4.5 分数只报整数） */
export function fmtGain(gain: number): string {
  if (!Number.isFinite(gain)) return '—'
  const rounded = Math.round(gain) || 0
  return rounded > 0 ? `+${rounded}` : String(rounded)
}

/** 性价比（分/CU）：两位小数，除零返回占位 */
export function fmtRatio(gain: number, cu: number): string {
  if (!Number.isFinite(gain) || !Number.isFinite(cu) || cu <= 0) return '—'
  return (Math.round((gain / cu) * 100) / 100).toFixed(2)
}

export function bandText(band: Band | null): string {
  return band === null ? '无档位' : `${band} 档`
}

/** 档位释义：只用于 12px 无衬线辅文，不替代档位字母 */
export function bandCaption(band: Band | null): string {
  switch (band) {
    case 'A':
      return '就绪 · 可直接发布'
    case 'B':
      return '可用 · 满足发布门槛'
    case 'C':
      return '待改进 · 低于门槛'
    case 'D':
      return '不可发布 · 缺口明显'
    default:
      return '样本不足 · 不出档位'
  }
}

export function docStateText(state: DocState): string {
  switch (state) {
    case 'COMPLETE':
      return '数据完整'
    case 'PARTIAL':
      return '样本不足'
    case 'DESIGN_ONLY':
      return '仅设计态'
  }
}

/** 导出文件名：ars_<skill>_<model>_<date>.json */
export function exportFileName(skill: string, model: string, date = new Date()): string {
  const day = `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}${String(
    date.getDate(),
  ).padStart(2, '0')}`
  const safe = (s: string) => s.replace(/[^a-zA-Z0-9._-]/g, '-')
  return `ars_${safe(skill)}_${safe(model)}_${day}.json`
}
