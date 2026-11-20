export const settingsSections = ['身份与权限', '策略表', '白名单与工具', '框架接入'] as const
export type SettingsSection = typeof settingsSections[number]
export function readSettingsLocation(hash: string) {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  if (params.get('section') !== '设置') return null
  const category = params.get('settings_category')
  const source = params.get('settings_source')
  return {
    category: settingsSections.includes(category as SettingsSection) ? category as SettingsSection : settingsSections[0],
    source: source === 'api' || source === 'preview' ? source as 'api' | 'preview' : null,
  }
}
export function writeSettingsLocation(category: SettingsSection, source: 'api' | 'preview') {
  return `#${new URLSearchParams({ section: '设置', settings_category: category, settings_source: source })}`
}
