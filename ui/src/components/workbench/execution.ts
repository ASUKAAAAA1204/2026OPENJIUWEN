import type { Gate } from './audit'
import { writeSessionHeaders } from './writeSession'

export type ExecutionEvent =
  | { seq: number; type: 'turn_started'; turn_id: string; run_id: string; segment: number }
  | { seq: number; type: 'assistant_delta'; turn_id: string; text: string }
  | { seq: number; type: 'tool_intent'; turn_id: string; decision_id: string; decision_class: string; tool_name: string; gate: Gate; summary: string; approval_id: string | null }
  | { seq: number; type: 'approval_requested'; turn_id: string; approval_id: string; decision_id: string; expires_at: string }
  | { seq: number; type: 'tool_result'; turn_id: string; decision_id: string; status: 'EXEC_FINISHED' | 'EXEC_FAILED' | 'UNKNOWN_OUTCOME'; summary: string; evidence_ref: string | null }
  | { seq: number; type: 'turn_completed'; turn_id: string; run_state: 'open' | 'degraded' | 'closed' }
  | { seq: number; type: 'turn_failed'; turn_id: string; outcome: 'known_failure' | 'unknown'; message: string }

export interface ExecutionInput { runId: string; segment: number; task: string; requestId: string; expectedExecutionRevision?: string }
export interface ExecutionState {
  source: 'ledger'; run_id: string; segment: number; revision: string
  status: 'idle' | 'active' | 'completed' | 'failed' | 'unknown' | 'review'
  request_id: string | null
}
export class ExecutionError extends Error { constructor(message: string, public readonly retryable = false, public readonly knownNotStarted = false) { super(message) } }
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown, max = 2000): value is string => typeof value === 'string' && !!value.trim() && value.length <= max
const gates: Gate[] = ['ALLOW', 'ASK', 'DENY', 'REWRITE', 'NONE']
function ensure(condition: unknown): asserts condition { if (!condition) throw new ExecutionError('运行事件不符合契约，已停止展示。') }
export function parseExecutionState(value: unknown, runId: string, segment: number): ExecutionState {
  if (!object(value) || value.source !== 'ledger' || value.run_id !== runId || value.segment !== segment ||
    !text(value.revision, 160) || !['idle', 'active', 'completed', 'failed', 'unknown', 'review'].includes(String(value.status)) ||
    (value.status === 'idle' ? value.request_id !== null : !text(value.request_id, 160))) {
    throw new ExecutionError('执行状态与当前运行或段号不一致，请重新核对。')
  }
  return value as unknown as ExecutionState
}

export function parseExecutionEvent(value: unknown, runId: string): ExecutionEvent {
  ensure(object(value) && Number.isSafeInteger(value.seq) && (value.seq as number) > 0 && text(value.turn_id, 160))
  switch (value.type) {
    case 'turn_started':
      ensure(value.run_id === runId && Number.isSafeInteger(value.segment) && (value.segment as number) > 0)
      return value as unknown as ExecutionEvent
    case 'assistant_delta':
      ensure(typeof value.text === 'string' && value.text.length > 0 && value.text.length <= 4000)
      return value as unknown as ExecutionEvent
    case 'tool_intent':
      ensure(text(value.decision_id, 160) && text(value.decision_class, 80) && text(value.tool_name, 160) && gates.includes(value.gate as Gate) && text(value.summary, 1000) && (value.approval_id === null || text(value.approval_id, 160)))
      ensure(value.gate === 'ASK' ? text(value.approval_id, 160) : value.approval_id === null)
      return value as unknown as ExecutionEvent
    case 'approval_requested':
      ensure(text(value.approval_id, 160) && text(value.decision_id, 160) && typeof value.expires_at === 'string' && Number.isFinite(Date.parse(value.expires_at)))
      return value as unknown as ExecutionEvent
    case 'tool_result':
      ensure(text(value.decision_id, 160) && ['EXEC_FINISHED', 'EXEC_FAILED', 'UNKNOWN_OUTCOME'].includes(String(value.status)) && text(value.summary, 1000) && (value.evidence_ref === null || text(value.evidence_ref, 500)))
      return value as unknown as ExecutionEvent
    case 'turn_completed':
      ensure(['open', 'degraded', 'closed'].includes(String(value.run_state)))
      return value as unknown as ExecutionEvent
    case 'turn_failed':
      ensure(['known_failure', 'unknown'].includes(String(value.outcome)) && text(value.message, 1000))
      return value as unknown as ExecutionEvent
    default:
      throw new ExecutionError('收到未知运行事件，已停止展示。')
  }
}

function parseFrame(frame: string): { id: number; data: unknown } | null {
  let id = '', event = '', data = ''
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'id') id = value
    else if (field === 'event') event = value
    else if (field === 'data') data += `${data ? '\n' : ''}${value}`
  }
  if (!data && !id && !event) return null
  ensure(event === 'run_event' && /^\d+$/.test(id) && !!data)
  let payload: unknown
  try { payload = JSON.parse(data) } catch { throw new ExecutionError('运行事件格式损坏，已停止展示。') }
  ensure(object(payload) && payload.seq === Number(id))
  return { id: Number(id), data: payload }
}

async function* readEvents(response: Response): AsyncGenerator<{ id: number; data: unknown }> {
  if (!response.body) throw new ExecutionError('运行服务没有提供事件流。', true)
  const reader = response.body.getReader(), decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() ?? ''
      if (buffer.length > 65536) throw new ExecutionError('运行事件超过单帧大小限制，连接已停止；执行结果仍需核对。')
      for (const frame of frames) {
        if (frame.length > 65536) throw new ExecutionError('运行事件超过单帧大小限制，连接已停止；执行结果仍需核对。')
        const parsed = parseFrame(frame)
        if (parsed) yield parsed
      }
      if (done) break
    }
    if (buffer.trim()) {
      const parsed = parseFrame(buffer)
      if (parsed) yield parsed
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

function responseError(status: number) {
  const messages: Record<number, string> = {
    401: '登录已失效，请重新登录后刷新运行。',
    403: '当前账号无权启动或续读此运行。',
    404: '运行执行服务尚未提供此接口。',
    409: '运行状态已变化或已有活动轮次，请刷新运行账本核对。',
    422: '当前运行或请求内容不满足执行条件，请核对服务端返回的运行状态。',
  }
  return new ExecutionError(messages[status] ?? '运行服务暂时不可用；请使用同一执行请求重新连接，不要新建执行。', status >= 500, [401, 403, 404, 409, 422].includes(status))
}

function terminal(event: ExecutionEvent) { return event.type === 'turn_completed' || event.type === 'turn_failed' }
function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason ?? new DOMException('Aborted', 'AbortError')) }, { once: true })
  })
}

export async function streamRunExecution(
  input: ExecutionInput,
  signal: AbortSignal,
  onEvent: (event: ExecutionEvent) => void,
  onReconnect: (attempt: number) => void,
  initialCursor = 0,
  initialTurnId: string | null = null,
) {
  let cursor = initialCursor, turnId = initialTurnId, attempts = 0
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(new DOMException('执行流超时', 'TimeoutError')), 300000)
  const cancel = () => controller.abort(signal.reason)
  signal.addEventListener('abort', cancel, { once: true })
  try {
    while (!controller.signal.aborted) {
      let response: Response
      try {
      let sessionHeaders: Record<string, string>
      try { sessionHeaders = await writeSessionHeaders(controller.signal) }
      catch (cause) { if (controller.signal.aborted) throw cause; throw new ExecutionError(cause instanceof Error ? cause.message : '无法核验当前会话，本次连接未建立。', false, true) }
      response = await fetch(`/api/v27/runs/${encodeURIComponent(input.runId)}/execute`, {
        method: 'POST', signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json', ...sessionHeaders, 'Idempotency-Key': input.requestId, ...(cursor ? { 'Last-Event-ID': String(cursor) } : {}) },
        body: JSON.stringify({ request_id: input.requestId, expected_segment: input.segment, expected_execution_revision: input.expectedExecutionRevision }),
      })
      if (!response.ok) throw responseError(response.status)
      if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('text/event-stream')) throw new ExecutionError('运行服务响应类型不正确；请用同一请求 ID 重新连接。', true)
      let sawEvent = false
      for await (const frame of readEvents(response)) {
        if (frame.id <= cursor) continue
        if (frame.id !== cursor + 1) throw new ExecutionError('运行事件序号不连续，请用同一请求 ID 重新连接。', true)
        const event = parseExecutionEvent(frame.data, input.runId)
        if (!turnId && event.type !== 'turn_started') throw new ExecutionError('首条运行事件缺少启动与运行绑定，已停止展示。')
        if (event.type === 'turn_started' && event.segment !== input.segment) throw new ExecutionError('运行服务返回了不匹配的段号，已停止展示。')
        if (turnId && event.turn_id !== turnId) throw new ExecutionError('重连返回了不同的运行轮次，已停止展示。')
        if (!turnId) turnId = event.turn_id
        cursor = frame.id; sawEvent = true; onEvent(event)
        if (terminal(event)) return { lastEventId: cursor, turnId, terminal: event }
      }
      if (sawEvent || cursor > 0) throw new ExecutionError('事件流提前结束，运行结果尚未确认。', true)
      throw new ExecutionError('运行服务返回了空事件流，执行状态未知。', true)
    } catch (cause) {
      if (controller.signal.aborted) throw cause
      if (cause instanceof ExecutionError && !cause.retryable) throw cause
      attempts++
      if (attempts > 3) throw new ExecutionError('运行连接中断，执行结果尚未确认。可以重新连接同一执行请求；不会重新提交新任务。')
      onReconnect(attempts)
      await wait(Math.min(250 * 2 ** (attempts - 1), 1000), controller.signal)
      }
    }
    throw controller.signal.reason ?? new DOMException('Aborted', 'AbortError')
  } finally {
    clearTimeout(timeout)
    signal.removeEventListener('abort', cancel)
  }
}

