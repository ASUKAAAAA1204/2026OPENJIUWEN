import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExecutionError, parseExecutionEvent, parseExecutionState, streamRunExecution } from '../src/components/workbench/execution'

const start = { seq: 1, type: 'turn_started', turn_id: 'TURN-1', run_id: 'RUN-1', segment: 2 }
const done = { seq: 2, type: 'turn_completed', turn_id: 'TURN-1', run_state: 'open' }
function sse(...events: object[]) {
  const body = events.map(event => {
    const value = event as { seq: number; type: string }
    return `id: ${value.seq}\nevent: run_event\ndata: ${JSON.stringify(value)}\n\n`
  }).join('')
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
}
function authFetch() {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ authenticated: true, csrf_token: 'csrf-run', identity: { user_id: 'u1', display_name: 'tester', role: '平台管理员', scope: 'workspace' } }), { headers: { 'Content-Type': 'application/json' } })))
}

describe('Agent 执行事件流契约', () => {
  it('执行状态绑定运行、段号和修订，既有请求不可伪装成idle', () => {
    const state = { source: 'ledger', run_id: 'RUN-1', segment: 2, revision: 'state-1', status: 'idle', request_id: null }
    expect(parseExecutionState(state, 'RUN-1', 2).status).toBe('idle')
    for (const status of ['active', 'completed', 'failed', 'unknown', 'review']) {
      expect(parseExecutionState({ ...state, status, request_id: 'REQ-1' }, 'RUN-1', 2).request_id).toBe('REQ-1')
      expect(() => parseExecutionState({ ...state, status }, 'RUN-1', 2)).toThrow()
    }
    for (const corrupt of [{ ...state, request_id: 'REQ-1' }, { ...state, revision: '' }, { ...state, status: 'arbitrary' }, { ...state, run_id: 'OTHER' }, { ...state, segment: 3 }]) {
      expect(() => parseExecutionState(corrupt, 'RUN-1', 2)).toThrow()
    }
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

  it('校验运行绑定、段号和脱敏事件字段', () => {
    expect(parseExecutionEvent(start, 'RUN-1')).toEqual(start)
    expect(parseExecutionEvent({ seq: 2, type: 'assistant_delta', turn_id: 'TURN-1', text: '\n  ' }, 'RUN-1')).toMatchObject({ text: '\n  ' })
    expect(() => parseExecutionEvent({ seq: 2, type: 'assistant_delta', turn_id: 'TURN-1', text: '' }, 'RUN-1')).toThrow(ExecutionError)
    expect(() => parseExecutionEvent({ ...start, run_id: 'RUN-2' }, 'RUN-1')).toThrow(ExecutionError)
    expect(() => parseExecutionEvent({ seq: 2, type: 'tool_result', turn_id: 'TURN-1', decision_id: 'D1', status: 'EXEC_FINISHED', summary: '', evidence_ref: null }, 'RUN-1')).toThrow(ExecutionError)
    expect(() => parseExecutionEvent({ seq: 2, type: 'unknown_tool_event', turn_id: 'TURN-1' }, 'RUN-1')).toThrow(ExecutionError)
  })

  it('同一请求 ID 续读，携带最后事件序号且忽略已收事件', async () => {
    authFetch()
    const requests: Request[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/v27/session') return new Response(JSON.stringify({ authenticated: true, csrf_token: 'csrf-run', identity: { user_id: 'u1', display_name: 'tester', role: '平台管理员', scope: 'workspace' } }))
      requests.push(new Request(new URL(String(input), 'http://localhost'), init))
      if (requests.length === 1) return sse(start)
      return sse(start, done)
    })
    vi.stubGlobal('fetch', fetcher)
    const received: object[] = []
    const reconnects: number[] = []
    const result = await streamRunExecution({ runId: 'RUN-1', segment: 2, task: 'review project', requestId: 'REQ-1', expectedExecutionRevision: 'state-1' }, new AbortController().signal, event => received.push(event), attempt => reconnects.push(attempt))
    expect(result.terminal).toEqual(done)
    expect(received).toEqual([start, done])
    expect(reconnects).toEqual([1])
    expect(requests).toHaveLength(2)
    for (const request of requests) {
      expect(request.headers.get('idempotency-key')).toBe('REQ-1')
      expect(request.headers.get('x-csrf-token')).toBe('csrf-run')
      expect(await request.json()).toEqual({ request_id: 'REQ-1', expected_segment: 2, expected_execution_revision: 'state-1' })
    }
    expect(requests[1].headers.get('last-event-id')).toBe('1')
  })

  it('连续短流断开时最多尝试三次重连', async () => {
    authFetch()
    const requests: Request[] = []
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/v27/session') return new Response(JSON.stringify({ authenticated: true, csrf_token: 'csrf-run', identity: { user_id: 'u1', display_name: 'tester', role: '平台管理员', scope: 'workspace' } }))
      requests.push(new Request(new URL(String(input), 'http://localhost'), init))
      return sse(start)
    })
    vi.stubGlobal('fetch', fetcher)
    const reconnects: number[] = []
    await expect(streamRunExecution({ runId: 'RUN-1', segment: 2, task: 'review project', requestId: 'REQ-RETRY' }, new AbortController().signal, () => {}, attempt => reconnects.push(attempt))).rejects.toThrow('连接中断')
    expect(requests).toHaveLength(4)
    expect(reconnects).toEqual([1, 2, 3])
  })

  it('ASK 事件保留审批绑定，结果事件完成后结束轮次', async () => {
    authFetch()
    const ask = { seq: 2, type: 'approval_requested', turn_id: 'TURN-1', approval_id: 'APP-1', decision_id: 'DEC-1', expires_at: '2026-11-19T20:00:00Z' }
    const result = { seq: 3, type: 'tool_result', turn_id: 'TURN-1', decision_id: 'DEC-1', status: 'EXEC_FINISHED', summary: 'operation finished', evidence_ref: 'EV-1' }
    const completed = { ...done, seq: 4 }
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input) === '/api/v27/session' ? new Response(JSON.stringify({ authenticated: true, csrf_token: 'csrf-run', identity: { user_id: 'u1', display_name: 'tester', role: '平台管理员', scope: 'workspace' } })) : sse(start, ask, result, completed))
    vi.stubGlobal('fetch', fetcher)
    const received: object[] = []
    await streamRunExecution({ runId: 'RUN-1', segment: 2, task: 'review project', requestId: 'REQ-ASK' }, new AbortController().signal, event => received.push(event), () => {})
    expect(received).toEqual([start, ask, result, completed])
  })

  it.each(['', '\n\n'])('超大完整或未结束帧停止读取并取消响应流 %j', async suffix => {
    const cancel = vi.fn()
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/v27/session') return new Response(JSON.stringify({ authenticated: true, csrf_token: 'csrf-run', identity: { user_id: 'u1', display_name: 'tester', role: '平台管理员', scope: 'workspace' } }))
      return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(`data: ${'x'.repeat(65537)}${suffix}`)) }, cancel }), { headers: { 'Content-Type': 'text/event-stream' } })
    })
    vi.stubGlobal('fetch', fetcher)
    await expect(streamRunExecution({ runId: 'RUN-1', segment: 2, task: 'review', requestId: 'REQ-LIMIT' }, new AbortController().signal, () => {}, () => {})).rejects.toThrow('单帧大小限制')
    expect(cancel).toHaveBeenCalledOnce()
  })
})
