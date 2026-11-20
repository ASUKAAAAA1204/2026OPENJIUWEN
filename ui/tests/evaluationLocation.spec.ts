import { expect, it } from 'vitest'
import { readEvaluationLocation, writeEvaluationLocation } from '../src/components/workbench/evaluationLocation'
it('评估定位往返编码版本与范围，仅接受白名单来源和有界标识', () => {
  const value = { source: 'api' as const, selected: '["技能","2.0","策略","白名单"]', scope: 'RUN/2', baseline: 'base', candidate: 'next' }
  expect(readEvaluationLocation(writeEvaluationLocation(value))).toEqual(value)
  expect(readEvaluationLocation('#section=设置')).toBeNull()
  expect(readEvaluationLocation('#section=版本评估&evaluation_source=unknown&evaluation_scope=%20bad&confirmed=true')).toEqual({ source: null, selected: '', scope: 'aggregate', baseline: '', candidate: '' })
  expect(writeEvaluationLocation({ ...value, selected: 'x'.repeat(2049) })).not.toContain('evaluation_version')
})
