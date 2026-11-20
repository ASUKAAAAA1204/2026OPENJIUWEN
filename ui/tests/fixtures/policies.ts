import type { PolicyCatalog, PolicySwitchReceipt } from '../../src/components/workbench/policies'
import { settingsPreview, type SettingsSnapshot } from '../../src/components/workbench/settings'
export function policyCatalogFixture(): PolicyCatalog {
  const initial = { version: 'policy-2.7.1', content_hash: 'a'.repeat(64), description: '高危写入人工复核基线', published_at: '2026-11-18T12:00:00Z', published_by: 'fixture-admin', eligible: true, reason: '合成已发布版本，非真实策略验证', configuration: { rules: [{ decision_class: 'FILE_WRITE', gate: 'ASK' as const, exec_policy: 'normal' as const, risk: 'high' as const, ask_timeout_seconds: 30, ask_timeout_policy: 'DENY' as const }], debt_review_threshold: 6, redaction_version: 'redaction-1' } }
  const next = { ...initial, version: 'policy-2.7.2', content_hash: 'b'.repeat(64), description: '高危写入阻断与债务阈值调整', published_at: '2026-11-19T12:00:00Z', configuration: { ...initial.configuration, rules: [{ decision_class: 'FILE_WRITE', gate: 'DENY' as const, exec_policy: 'normal' as const, risk: 'high' as const, ask_timeout_seconds: null, ask_timeout_policy: null }], debt_review_threshold: 4 } }
  return { source: 'ledger', revision: 'policy-catalog-1', can_switch: true, active: { version: initial.version, content_hash: initial.content_hash }, versions: [initial, next] }
}
export function policySettingsFixture(catalog: PolicyCatalog, role: NonNullable<SettingsSnapshot['identity']>['role'] = '平台管理员'): SettingsSnapshot {
  const active = catalog.versions.find(item => item.version === catalog.active?.version)
  return { ...settingsPreview(), source: 'ledger', revision: `settings-${catalog.revision}`, identity: { user_id: 'fixture-admin', display_name: '验收用户', role, scope: '合成工作空间' }, policy: active ? { version: active.version, content_hash: active.content_hash, rules: active.configuration.rules, history: [] } : null }
}
export function policyReceiptFixture(previous: PolicyCatalog, requestId = 'request-1', reason = '核对后切换', targetIndex = 1): PolicySwitchReceipt {
  const catalog = structuredClone(previous), target = catalog.versions[targetIndex]
  catalog.revision = `${previous.revision}-changed`; catalog.active = { version: target.version, content_hash: target.content_hash }
  return { source: 'ledger', request_id: requestId, catalog, audit: { audit_id: 'POLICY-AUDIT-1', actor_id: 'fixture-admin', at: '2026-11-19T13:00:00Z', reason, from_version: previous.active?.version ?? null, from_hash: previous.active?.content_hash ?? null, to_version: target.version, to_hash: target.content_hash } }
}
