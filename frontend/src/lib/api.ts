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
}

export interface Health {
  api: boolean
  graph: boolean
  llm: boolean
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
