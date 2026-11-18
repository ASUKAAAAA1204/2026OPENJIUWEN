import type { Report } from '../api/types'
import pre from './pre.json'
import post from './post.json'
import partial from './partial.json'
import design from './design.json'
import edge from './edge.json'

export type MockScenario = 'pre' | 'post' | 'partial' | 'design' | 'edge'

const MOCKS: Record<MockScenario, unknown> = { pre, post, partial, design, edge }

const SCENARIOS: MockScenario[] = ['pre', 'post', 'partial', 'design', 'edge']

/** ?mock= 选择器；缺省与非法值一律回落 pre（演示主线） */
export function resolveScenario(search: string): MockScenario {
  const value = new URLSearchParams(search).get('mock')
  return SCENARIOS.includes(value as MockScenario) ? (value as MockScenario) : 'pre'
}

export function loadMock(scenario: MockScenario): Report {
  return MOCKS[scenario] as Report
}
