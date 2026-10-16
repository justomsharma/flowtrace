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
  /** The case's background investigation while it is queued or running. */
  job?: JobBrief | null
  /** Its background investigation ended in an error; it can be run again. */
  failed?: boolean
  /** Currency of the case's own payments (US Dollar when mixed) and its units per US dollar. */
  currency?: string
  rate?: number
}

export interface JobBrief { id: string; status: 'queued' | 'running' }

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
  /** True for the hand-picked demo files in data/samples (far more laundering than a real day). */
  curated?: boolean
  detector_hits: number
  timings_ms: Record<string, number>
  has_labels: boolean
  labelled_laundering: number | null
  cases: CaseBrief[]
  /** Top cases investigated in the background after the upload. */
  prefetch?: { total: number; done: number; failed: number }
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
    if (e.type === 'done' || e.type === 'error' || e.type === 'cancelled') {
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

// --- actions: the agent proposes, a person decides --------------------------------------------

export type ActionType = 'HOLD' | 'RELEASE' | 'ESCALATE' | 'FILE_STR' | 'CLOSE'
/** PROVISIONAL: an auto-hold the agent placed; a person confirms (approve) or rejects it, or it is released when
 *  provisional_until passes. */
export type ActionStatus = 'PROPOSED' | 'PROVISIONAL' | 'APPROVED' | 'REJECTED' | 'EXPIRED'

export interface Action {
  id: string
  type: ActionType
  status: ActionStatus
  case_id: string
  account?: string
  /** Money the hold freezes: the case money traced into the account (a lien on the amount, not the account). */
  hold_amount?: number
  /** Assumption: money upstream that would have to pass this account if the mules keep their routes. */
  on_routes?: number
  /** Currency of the case's money trail, and its units per US dollar (graph amounts are in US dollars). */
  currency?: string
  rate?: number
  why?: string
  evidence_ids?: string[]
  tx_ids?: string[]
  expiry_days?: number
  expires_ts?: number
  /** 'agent' = the FlowTrace app; 'mcp' = an outside AI client through the MCP server. Never a person. */
  proposed_by: 'agent' | 'mcp'
  proposed_at: number
  decided_by?: string
  decided_at?: number
  reason?: string
  released_at?: number
  /** Auto-hold: who placed it ("agent (auto-hold)"), when, until when it waits for a person, and why. */
  held_by?: string
  held_at?: number
  provisional_until?: number
  hold_reason?: string
}

/** Whether the agent may hold money provisionally on this server, and when (actions.auto_hold + APPROVER_TOKEN). */
export interface AutoHoldPolicy { active: boolean; min_confidence: number | null; confirm_business_days: number | null }

export interface HoldProposal {
  account: string
  hold: number
  on_routes: number
  why: string
  evidence_ids: string[]
  tx_ids: string[]
  expiry_days: number
}

export interface HoldPlan {
  case_id: string
  decided_at: number
  disposition: string | null
  /** Case money still at rest in the trail (what holds could freeze, over every account). */
  in_motion: number
  /** Headline: what the planned holds freeze now. */
  frozen_now: number
  /** Assumption, reported apart: money on the case's routes the holds would also stop. */
  on_routes: number
  already_out: number
  /** Account-wide freezes that would cut every route (context; liens on empty accounts freeze nothing). */
  route_cut_size: number
  candidates: string[]
  holds: HoldProposal[]
  case_action: { type: ActionType; why: string } | null
  skipped_hubs: string[]
  currency: string
  rate: number
  note: string
  network: {
    nodes: { id: string; at_rest: number; held: boolean; exit: boolean; hub: boolean }[]
    edges: { tx_id: string; src: string; dst: string; amount: number; ts: number; kind: 'trail' | 'exit' | 'hub' }[]
  }
}

export interface AuditEntry {
  id: string
  ts: number
  actor: string
  event: 'PROPOSED' | 'PROVISIONAL' | 'APPROVED' | 'REJECTED' | 'AUTO_RELEASED' | 'RELEASED' | 'VERDICT' | 'CASE_REUPLOADED' | 'SUPERSEDED' | 'WITHDRAWN'
  /** How the approver was checked ('approver-token'); set on decisions only. */
  auth?: string
  action_id: string
  case_id: string
  account?: string
  from_status: string
  to_status: string
  reason?: string
}

/** What the holds still open freeze now, and the money on the case's routes they would also stop (assumption). */
export interface OpenEffect { frozen: number; on_routes: number }

/** The approver token for approve/reject, from the dev config (frontend/.env.development.local). */
export const approverToken: string | undefined = import.meta.env.VITE_APPROVER_TOKEN || undefined

/** POST a JSON body. The idempotency key makes a repeated click return the first answer instead of acting twice. */
export async function sendJSON<T>(path: string, body?: unknown, opts: { key?: string; approverToken?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (opts.key) headers['Idempotency-Key'] = opts.key
  if (opts.approverToken) headers['X-Approver-Token'] = opts.approverToken
  const r = await fetch(path, { method: 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!r.ok) {
    const text = await r.text()
    let msg = text
    try { msg = (JSON.parse(text) as { detail?: string }).detail ?? text } catch { /* not JSON */ }
    throw new Error(typeof msg === 'string' ? msg : `${r.status}`)
  }
  return r.json() as Promise<T>
}

// --- case overview (GET /api/cases/{id}/overview) ---------------------------------------------

export type PaymentStatus = 'flagged' | 'trail' | 'normal'
export type Role = 'Sender' | 'Intermediary' | 'Destination'
export type OverviewState = 'not_started' | 'queued' | 'investigating' | 'failed' | 'escalate' | 'review' | 'close'

export interface FlowNode {
  id: string
  role: Role
  /** Sent for a sender, received otherwise (US dollars). */
  amount: number
  sent: number
  received: number
  in_case: boolean
  hold: 'proposed' | 'provisional' | 'approved' | null
  entity_type: string | null
}

export interface FlowEdge {
  src: string
  dst: string
  amount: number
  count: number
  status: PaymentStatus
  /** Carried case money into an account proposed for a hold, or held. */
  hold: boolean
  first_ts: number
  last_ts: number
}

/** amount is the USD equivalent; cur and amount_orig are the payment in its own currency. */
export interface Payment {
  tx_id: string; ts: number; src: string; dst: string; amount: number; fmt: string | null; status: PaymentStatus
  cur?: string | null; amount_orig?: number | null
}

export interface Finding {
  id: string
  code: string
  claim: string
  /** One short line (12 words or fewer) for the case page, naming the account ("Account A"). */
  brief: string
  /** Measured strength: Strong sign / Moderate sign / Common in normal accounts / Strong sign of normal, or empty. */
  tag: string
  /** Why it matters, with the measured rate when there is one. */
  why: string
  /** What an analyst checks to rule the sign out (red flags only). */
  check: string
  weight: number
  confidence: 'High' | 'Medium' | 'Low'
  escalator: boolean
  accounts: string[]
  tx_ids: string[]
}

export interface CaseOverview {
  case_id: string
  batch_id: string | null
  primary: string
  typology: string | null
  tier: 'today' | 'lower' | null
  case_score: number | null
  ml_score: number | null
  priority: 'High' | 'Medium' | 'Low'
  window: [number, number]
  uploaded_at: number | null
  currency: string
  rate: number
  /** risk_level and confidence are High / Medium / Low at the config's thresholds; the confidence score is not a probability. */
  status: { state: OverviewState; job: JobBrief | null; risk: number | null; risk_level: string | null; confidence: string | null; reasons: string[] }
  kpis: { amount: number; accounts: number; payments: number; connected_accounts: number; traced_accounts: number; suspicious_patterns: number | null }
  flow: { nodes: FlowNode[]; edges: FlowEdge[]; hidden_accounts: number }
  transactions: Payment[]
  timeline: { bucket: 'hour' | 'day'; bins: { t: number; flagged: number; other: number; n: number }[] }
  distribution: { account: string | null; amount: number; share: number; accounts?: number }[]
  findings: Finding[]
  explanations: Finding[]
  indicators: { key: 'model' | 'red_flags' | 'shape' | 'legitimacy'; value: number; contribution: number }[]
  /** Why the case was flagged before any investigation: detectors that fired, then the model. */
  alert_reasons: string[]
  /** Likely underlying crime, guessed from the laundering shape (the data records no crime type). */
  /** Possible predicate crimes (indicative); escalated cases only. */
  crime_guess: string[]
  auto_hold?: AutoHoldPolicy
  /** Account id -> "Account A", the same names everywhere on the page and in the report. */
  names: Record<string, string>
  /** Two or three plain sentences: what moved, the pattern, and what the investigation concluded. */
  story: string[]
  /** One sentence naming what decided the verdict. */
  verdict_basis: string | null
  summary: { headline: string; text: string; points: string[]; by_ai: boolean } | null
  next_steps: string[]
  steps: StepInfo[]
}

/** "9 Sep 2022, 13:37 UTC": a payment's time. The dataset's timestamps carry no time zone, so they stay in UTC. */
export const fmtUtc = (ms: number, withDate = true) =>
  new Date(ms).toLocaleString('en-GB', {
    ...(withDate ? { day: 'numeric', month: 'short', year: 'numeric' } : {}), hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
  }) + ' UTC'
