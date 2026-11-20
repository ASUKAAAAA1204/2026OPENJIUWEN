import { describe, expect, it, vi, afterEach } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import {
  loadAudit,
  parseAuditSnapshot,
  useAuditSource,
  type AuditMode,
} from '../src/components/workbench/auditSource'
import { createPreview } from '../src/components/workbench/model'
import { previewDecisions } from '../src/components/workbench/audit'
const run = createPreview('RUN-1', '检查', 'openJiuwen')
function snapshot() {
  return {
    source: 'ledger',
    run_id: run.id,
    state: 'closed',
    segment: 1,
    parent_run_id: null,
    child_run_ids: [] as string[],
    decisions: previewDecisions(run),
    chain: { status: 'unverified', reason: '无检查点' },
  }
}
afterEach(() => vi.unstubAllGlobals())
describe('账本数据源', () => {
  it('证据正文的指纹与时间需符合格式，缺失材料用null明确表达', () => {
    const data = snapshot(), record = data.decisions[1]
    record.evidence_ref = 'EVIDENCE-VALID'
    record.evidence = { evidence_id: 'EVIDENCE-VALID', input_digest: '', output_digest: '', model_id: 'fixture', model_params: '{}', verdict: 'ok', ts: '2026-11-19T12:00:00Z', content_hash: 'a'.repeat(64) }
    expect(parseAuditSnapshot(data, run.id).decisions[1].evidence?.content_hash).toBe('a'.repeat(64))
    record.evidence.content_hash = 'text pretending to be a hash'
    expect(() => parseAuditSnapshot(data, run.id)).toThrow()
    record.evidence.content_hash = null
    record.evidence.ts = '2026-11-19T12:00:00'
    expect(() => parseAuditSnapshot(data, run.id)).toThrow()
    record.evidence.ts = null
    expect(parseAuditSnapshot(data, run.id).decisions[1].evidence?.ts).toBeNull()
  })
  it('拒绝跨运行、重复决策、错误枚举及不匹配的证据引用', () => {
    expect(() => parseAuditSnapshot(snapshot(), 'OTHER')).toThrow()
    const duplicate = snapshot()
    duplicate.decisions.push(duplicate.decisions[0])
    expect(() => parseAuditSnapshot(duplicate, run.id)).toThrow()
    const invalid = snapshot()
    invalid.decisions[0].gate = 'PASS' as never
    expect(() => parseAuditSnapshot(invalid, run.id)).toThrow()
    const cycle = snapshot(); cycle.child_run_ids.push(run.id)
    expect(() => parseAuditSnapshot(cycle, run.id)).toThrow()
    const duplicateChild = snapshot(); duplicateChild.child_run_ids = ['CHILD', 'CHILD']
    expect(() => parseAuditSnapshot(duplicateChild, run.id)).toThrow()
  })
  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not-found'],
    [500, 'error'],
  ])('HTTP %s保持明确错误，不回退预览', async (status, state) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: Number(status) })))
    await expect(loadAudit(run, 'api', new AbortController().signal)).rejects.toMatchObject({
      state,
    })
  })
  it('切换来源后丢弃旧请求，卸载时取消请求', async () => {
    let resolveRequest: (value: Response) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveRequest = resolve
          }),
      ),
    )
    const scope = effectScope()
    const mode = ref<AuditMode>('api')
    const resource = scope.run(() => useAuditSource(ref(run), mode))!
    expect(resource.state.value).toBe('loading')
    mode.value = 'preview'
    await nextTick()
    await Promise.resolve()
    expect(resource.data.value?.source).toBe('frontend_preview')
    resolveRequest(new Response(JSON.stringify(snapshot())))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(resource.data.value?.source).toBe('frontend_preview')
    scope.stop()
  })
})
