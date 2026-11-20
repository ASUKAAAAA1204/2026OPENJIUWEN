import { describe, expect, it } from 'vitest'
import { readDecisionLocation, writeDecisionLocation, readRunLocation, readWorkspaceSection, workspaceSections, writeRunLocation, writeWorkspaceLocation } from '../src/components/workbench/runLocation'
import { relatedRun } from '../src/components/workbench/runs'
describe('运行深链接', () => {
  it('决策与视图仅序列化白名单位置，错误运行与无效值安全回退', () => {
    const run = relatedRun('RUN-1', 'LangGraph')
    const hash = writeDecisionLocation(run, 'DEC:2', '证据与账本')
    expect(readDecisionLocation(hash, run.id)).toEqual({ decision: 'DEC:2', view: '证据与账本' })
    expect(readDecisionLocation(hash, 'OTHER')).toEqual({ decision: '', view: '判定与参数' })
    expect(readDecisionLocation('#run=RUN-1&decision=%20bad&view=unknown', run.id)).toEqual({ decision: '', view: '判定与参数' })
    expect([...new URLSearchParams(hash.slice(1)).keys()]).toEqual(['run', 'framework', 'decision', 'view'])
  })
  it('六个板块仅保存白名单位置，拒绝任意页面和敏感参数', () => {
    for (const section of workspaceSections) {
      expect(readWorkspaceSection(writeWorkspaceLocation(section))).toBe(section)
      expect([...new URLSearchParams(writeWorkspaceLocation(section).slice(1)).keys()]).toEqual(['section'])
    }
    for (const hash of ['#', '#section=unknown', '#section=运行详情', '#password=secret', '#section=%20设置']) expect(readWorkspaceSection(hash)).toBeNull()
  })
  it('仅序列化运行ID与框架并可恢复', () => {
    const run = relatedRun('RUN:private?abc', 'LangGraph'), hash = writeRunLocation(run)
    expect(hash).toContain('RUN%3Aprivate%3Fabc')
    expect(readRunLocation(hash)?.id).toBe(run.id)
    expect(hash).not.toContain('private title')
  })
  it('拒绝缺少标识、空白、过长或未知框架', () => {
    for (const hash of ['#', '#run=%20&framework=LangGraph', `#run=${'x'.repeat(513)}&framework=LangGraph`, '#run=RUN-1&framework=unknown']) expect(readRunLocation(hash)).toBe(null)
  })
})
