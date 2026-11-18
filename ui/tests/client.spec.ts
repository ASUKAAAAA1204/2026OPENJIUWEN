import { describe, expect, it, vi } from 'vitest'

vi.mock('axios', () => ({ default: { get: vi.fn() } }))

import axios from 'axios'
import { getReport, resolveSource } from '../src/api/client'

describe('resolveSource', () => {
  it('默认 mock + pre', () => {
    expect(resolveSource('')).toEqual({ mode: 'mock', scenario: 'pre' })
  })

  it('?mode=api 切真实接口，场景照常解析', () => {
    expect(resolveSource('?mode=api&mock=post')).toEqual({ mode: 'api', scenario: 'post' })
    expect(resolveSource('?mode=api&mock=nope')).toEqual({ mode: 'api', scenario: 'pre' })
  })
})

describe('getReport', () => {
  it('mock 模式不走 axios', async () => {
    const report = await getReport({ mode: 'mock', scenario: 'post' })
    expect(report.score).toBe(79)
    expect(axios.get).not.toHaveBeenCalled()
  })

  it('api 模式请求 /api/report 并透传场景', async () => {
    const payload = { score: 47 } as never
    vi.mocked(axios.get).mockResolvedValue({ data: payload })
    const report = await getReport({ mode: 'api', scenario: 'pre' })
    expect(report).toBe(payload)
    expect(axios.get).toHaveBeenCalledWith('/api/report', { params: { scenario: 'pre' } })
  })
})
