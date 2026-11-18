/** 数据契约 —— 唯一来源。任何字段改动必须先改此文件并通知后端守门人。 */

export type DimState = 'VALUE' | 'NA' | 'MISSING'
export type DocState = 'DESIGN_ONLY' | 'PARTIAL' | 'COMPLETE'
export type Band = 'A' | 'B' | 'C' | 'D'
export type GateDecision = 'BLOCK' | 'PASS'

export interface Coord {
  skill_version: string
  model: string
  data_profile: string
  data_digest?: string
}

export interface Dim {
  key: 'L' | 'C' | 'R' | 'D' | 'F'
  /** 可定位 / 可编译 / 可复算 / 确定性 / 新鲜度 */
  label: string
  /** 25 / 25 / 20 / 15 / 15 */
  weight: number
  num: number
  den: number
  state: DimState
}

export interface Deduction {
  dim: string
  text: string
  action: string
  n: number
  price_cu: number
}

export type ClaimStatus = 'LOCATED' | 'UNLOCATED' | 'UNVERIFIABLE'

/** 证据下钻的最小单元；后端接入后随 Report.claims 下发 */
export interface Claim {
  id: string
  dim: 'L' | 'C' | 'R' | 'D' | 'F'
  text: string
  status: ClaimStatus
  locator: string | null
  digest: string
  fail_code?: string
}

export interface PlanAction {
  act: string
  label: string
  n: number
  cu: number
  gain: number
}

/** 预算档位：同一方案在不同预算下的重算预测 */
export interface PlanTier {
  cu: number
  predicted: number | null
}

export interface Plan {
  current: number
  target_band: Band
  actions: PlanAction[]
  total_cu: number
  /** 含 cu_buffer_pct 的报价 CU（后端下发，前端只展示） */
  budget_cu: number
  cu_buffer_pct: number
  predicted: number | null
  /** 预算点列（0 / 8 / 16 / 24 / 34 CU）；末项为完整方案 */
  tiers: PlanTier[]
}

export interface Report {
  coord: Coord
  doc_state: DocState
  score: number | null
  band: Band | null
  counts: { claims: number; rules: number; checks: number; rules_compilable?: number }
  dims: Dim[]
  deductions: Deduction[]
  plan: Plan
  trend: Array<{ cu: number; score: number }>
  /** 边际跌破 0.5 分/CU 的起点 CU；null = 未枯竭（后端下发） */
  marginal_exhausted_from: number | null
  gate: { threshold: number; decision: GateDecision; reason_code?: string }
  warnings: string[]
}
