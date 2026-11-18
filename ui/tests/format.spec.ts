import { describe, expect, it } from 'vitest'
import {
  bandCaption,
  bandText,
  docStateText,
  exportFileName,
  fmtCu,
  fmtGain,
  fmtRatio,
  fmtScore,
} from '../src/utils/format'

describe('fmtScore', () => {
  it('空值显示占位横线', () => {
    expect(fmtScore(null)).toBe('—')
    expect(fmtScore(Number.NaN)).toBe('—')
  })

  it('只报整数', () => {
    expect(fmtScore(47)).toBe('47')
    expect(fmtScore(46.5)).toBe('47')
  })
})

describe('fmtCu', () => {
  it('整数省略小数位', () => {
    expect(fmtCu(34)).toBe('34')
    expect(fmtCu(34.0)).toBe('34')
  })

  it('保留一位小数', () => {
    expect(fmtCu(6.3)).toBe('6.3')
    expect(fmtCu(1.05)).toBe('1.1')
  })

  it('非有限值显示占位', () => {
    expect(fmtCu(Number.POSITIVE_INFINITY)).toBe('—')
  })
})

describe('fmtGain', () => {
  it('正数带加号、取整', () => {
    expect(fmtGain(11.25)).toBe('+11')
    expect(fmtGain(4)).toBe('+4')
  })

  it('零与负数不带加号', () => {
    expect(fmtGain(0)).toBe('0')
    expect(fmtGain(-4.5)).toBe('-4')
    expect(fmtGain(-0.004)).toBe('0')
  })

  it('非有限值显示占位', () => {
    expect(fmtGain(Number.NaN)).toBe('—')
  })
})

describe('fmtRatio', () => {
  it('除零与非法输入返回占位', () => {
    expect(fmtRatio(4, 0)).toBe('—')
    expect(fmtRatio(4, -1)).toBe('—')
    expect(fmtRatio(Number.NaN, 2)).toBe('—')
  })

  it('两位小数', () => {
    expect(fmtRatio(11.25, 6.3)).toBe('1.79')
    expect(fmtRatio(4, 4)).toBe('1.00')
  })
})

describe('文案映射', () => {
  it('档位与释义', () => {
    expect(bandText('B')).toBe('B 档')
    expect(bandText(null)).toBe('无档位')
    expect(bandCaption('A')).toBe('就绪 · 可直接发布')
    expect(bandCaption(null)).toBe('样本不足 · 不出档位')
  })

  it('文档状态', () => {
    expect(docStateText('COMPLETE')).toBe('数据完整')
    expect(docStateText('PARTIAL')).toBe('样本不足')
    expect(docStateText('DESIGN_ONLY')).toBe('仅设计态')
  })
})

describe('exportFileName', () => {
  it('日期与非法字符', () => {
    expect(exportFileName('assurance-skill@2.2.0', 'deepseek/v4', new Date(2026, 10, 2))).toBe(
      'ars_assurance-skill-2.2.0_deepseek-v4_20261102.json',
    )
  })
})
