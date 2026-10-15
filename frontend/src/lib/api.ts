export interface Overview {
  totals: {
    transactions: number
    laundering: number
    accounts: number
    volume_usd: number
    pattern_attempts: number
    pattern_transactions: number
  }
  daily: { date: string; transactions: number; laundering: number }[]
  by_format: { payment_format: string; transactions: number; laundering: number; rate_pct: number }[]
  typologies: { typology: string; attempts: number; transactions: number }[]
  entity_types: { entity_type: string; accounts: number }[]
  relationship_history: { bucket: string; transactions: number; laundering: number; rate_pct: number }[]
}

export interface SplitMetrics {
  n: number
  positives: number
  threshold: number
  pr_auc: number
  roc_auc: number
  f1: number
  precision: number
  recall: number
  accuracy: number
  accuracy_all_legit_baseline: number
  flagged: number
  precision_at_100: number
  precision_at_500: number
  precision_at_1000: number
}

export interface ModelInfo {
  metrics: {
    split: Record<string, [string, string]>
    val: SplitMetrics
    test: SplitMetrics
    feature_importance_gain: Record<string, number>
  }
  curves?: {
    threshold: number
    threshold_sweep: { threshold: number; alerts: number; tp: number; precision: number; recall: number }[]
    pr_curve: { recall: number; precision: number }[]
    score_hist: { bins: number[]; legit: number[]; laundering: number[] }
    confusion: { tp: number; fp: number; fn: number; tn: number }
  }
  typology?: {
    attempts: number
    accuracy: number
    attempts_3plus_accounts: number
    accuracy_3plus_accounts: number
    per_class: Record<string, { attempts: number; recall: number }>
  }
  agent_eval?: {
    sample: string
    cases: number
    policies: Record<string, Record<string, number | null>>
  }
}

export interface Health {
  api: boolean
  graph: boolean
  llm: boolean
  model?: string
}

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const fmtInt = (n: number) => n.toLocaleString('en-US')
export const fmtPct = (x: number, d = 1) => `${(x * 100).toFixed(d)}%`
export const fmtCompact = (n: number) =>
  Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n)

// --- investigation -------------------------------------------------------------------------

export interface ShapeInfo {
  typology: string
  confidence: number
  why: string
  stats?: Record<string, number | boolean>
}

export interface DetectorHit {
  detector: string
  account: string
  strength: number
  detail: string
}

export interface CaseBrief {
  case_id: string
  primary: string
  accounts: string[]
  ml_score: number
  /** Chance at least one of the case's signals is right (calibrated model + detector precision). */
  case_score: number
  /** "today" = within the team's daily capacity; "lower" = listed below, never hidden. */
  tier: 'today' | 'lower'
  signals: { signal: string; p: number }[]
  shape: ShapeInfo
  amount: number
  detector_hits: DetectorHit[]
  ground_truth: number | null
  n_tx: number
  result?: { action: string; risk: number; confidence: string } | null
}

export interface BatchSummary {
  batch_id: string
  filename: string
  rows: number
  accounts: number
  window: [number, number]
  volume_usd: number
  score_histogram: number[]
  flagged_tx: number
  capacity: number
  detector_hits: number
  timings_ms: Record<string, number>
  has_labels: boolean
  labelled_laundering: number | null
  cases: CaseBrief[]
}

export interface Edge {
  tx_id: string
  src: string
  dst: string
  ts: number
  amount: number
  fmt?: string
  role?: string
  hop?: number
  p?: number
}

export interface GNode {
  id: string
  role?: string
  entity_type?: string | null
}

export interface EvidenceItem {
  id: string
  kind: 'support' | 'counter' | 'context'
  code: string
  claim: string
  weight: number
  tool: string
  step: number
  tx_ids: string[]
  accounts: string[]
  escalator: boolean
  metrics: Record<string, unknown>
}

export interface ReportItem {
  text: string
  evidence_ids: string[]
}

export interface Report {
  headline: string
  executive_summary: ReportItem
  subject_profile: ReportItem
  money_trail: ReportItem[]
  typology_assessment: ReportItem
  red_flags: ReportItem[]
  mitigating_factors: ReportItem[]
  unknowns: string[]
  next_steps: string[]
  str_narrative: string
  author?: string
  validation: { statements_checked: number; unsupported: number; unsupported_rate: number; dropped: { section: string; text: string; why: string }[] }
}

export interface DispositionInfo {
  action: 'ESCALATE' | 'REVIEW' | 'CLOSE'
  risk: number
  confidence: number
  confidence_label: string
  components: Record<string, number>
  reasons: string[]
}

export interface StepInfo {
  step: number
  tool: string
  args: Record<string, unknown>
  summary: Record<string, unknown>
  error: string | null
  reason: string
  ms: number
}

export interface QueryInfo {
  step: number
  label: string
  cypher: string
  params: Record<string, unknown>
  ms: number
  rows: number
}

export interface CaseResult {
  case_id: string
  primary: string
  accounts: string[]
  ml_score: number
  shape: ShapeInfo
  crime_hypotheses: { crime: string; basis: string }[]
  disposition: DispositionInfo
  report: Report
  evidence: EvidenceItem[]
  steps: StepInfo[]
  queries: QueryInfo[]
  graph: { nodes: GNode[]; edges: Edge[] }
  stats: Record<string, unknown>
}

export interface CaseInput {
  case_id: string
  primary: string
  accounts: string[]
  edges: Edge[]
  t0: number
  t1: number
  ml_score: number
  shape: ShapeInfo
  detector_hits: DetectorHit[]
  ground_truth: number | null
  amount: number
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type JobEvent = { seq: number; t: number; type: string } & Record<string, any>

export async function postJSON<T>(path: string, body?: FormData): Promise<T> {
  const r = await fetch(path, { method: 'POST', body })
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

/** Subscribe to a job's event stream; returns a function that closes it. */
export function streamJob(jobId: string, onEvent: (e: JobEvent) => void, onEnd?: () => void): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/events`)
  es.onmessage = (m) => {
    const e = JSON.parse(m.data) as JobEvent
    onEvent(e)
    if (e.type === 'done' || e.type === 'error') {
      es.close()
      onEnd?.()
    }
  }
  es.onerror = () => {
    es.close()
    onEnd?.()
  }
  return () => es.close()
}

export const fmtUsd = (n: number) =>
  n >= 1e6 ? `$${fmtCompact(n)}` : `$${Math.round(n).toLocaleString('en-US')}`
export const fmtTime = (ms: number) =>
  new Date(ms).toISOString().slice(5, 16).replace('T', ' ')
