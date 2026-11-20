import { expect, it } from 'vitest'
import { readSettingsLocation, settingsSections, writeSettingsLocation } from '../src/components/workbench/settingsLocation'
it('仅恢复设置白名单分类和来源，不从链接恢复凭据或提交确认', () => {
  for (const category of settingsSections) for (const source of ['api', 'preview'] as const) {
    expect(readSettingsLocation(writeSettingsLocation(category, source))).toEqual({ category, source })
  }
  expect(readSettingsLocation('#section=运行记录&settings_source=api')).toBeNull()
  expect(readSettingsLocation('#section=设置&settings_category=evil&settings_source=evil&password=secret&confirmed=true')).toEqual({ category: '身份与权限', source: null })
  expect(writeSettingsLocation('策略表', 'api')).not.toMatch(/password|confirmed|reason|token/)
})
