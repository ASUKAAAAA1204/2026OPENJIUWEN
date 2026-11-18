import { describe, expect, it } from 'vitest'
import { loadMock, resolveScenario } from '../src/mock'

describe('resolveScenario', () => {
  it('缺省回落 pre', () => {
    expect(resolveScenario('')).toBe('pre')
    expect(resolveScenario('?mock=')).toBe('pre')
  })

  it('合法值原样返回', () => {
    expect(resolveScenario('?mock=post')).toBe('post')
    expect(resolveScenario('?mock=partial')).toBe('partial')
  })

  it('非法值回落 pre', () => {
    expect(resolveScenario('?mock=unknown')).toBe('pre')
  })
})

describe('loadMock', () => {
  it('pre 是 47 分 D 档', () => {
    const report = loadMock('pre')
    expect(report.score).toBe(47)
    expect(report.band).toBe('D')
    expect(report.gate.decision).toBe('BLOCK')
  })

  it('post 是 79 分 B 档且放行', () => {
    const report = loadMock('post')
    expect(report.score).toBe(79)
    expect(report.band).toBe('B')
    expect(report.gate.decision).toBe('PASS')
  })

  it('partial 不出总分', () => {
    const report = loadMock('partial')
    expect(report.score).toBeNull()
    expect(report.band).toBeNull()
  })
})
