import axios from 'axios'
import type { Report } from './types'
import { loadMock, resolveScenario, type MockScenario } from '../mock'

export interface ReportSource {
  mode: 'mock' | 'api'
  scenario: MockScenario
}

/** ?mode=api 切真实接口；默认 mock，保证离线演示 */
export function resolveSource(search: string): ReportSource {
  const params = new URLSearchParams(search)
  return {
    mode: params.get('mode') === 'api' ? 'api' : 'mock',
    scenario: resolveScenario(search),
  }
}

export async function getReport(source: ReportSource): Promise<Report> {
  if (source.mode === 'mock') return loadMock(source.scenario)
  const res = await axios.get<Report>('/api/report', { params: { scenario: source.scenario } })
  return res.data
}
