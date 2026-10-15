import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, Loader2, Upload } from 'lucide-react'
import LiveGraph3D, { type MapLink } from '../components/LiveGraph3D'
import { ActionBadge } from '../components/badges'
import {
  fmtInt, fmtUsd, getJSON, postJSON, streamJob, type BatchSummary, type CaseBrief, type CaseResult, type JobEvent,
} from '../lib/api'
import { STAGE_PLAIN, STEP_PLAIN, trickName } from '../lib/plain'

interface GraphLink { tx_id: string; source: string; target: string; amount: number; p: number; case: string | null }
interface LiveStep { step: number; tool: string; reason: string; done: boolean; found: string[] }

const panel = { background: 'color-mix(in srgb, var(--surface) 88%, transparent)', border: '1px solid var(--border)', backdropFilter: 'blur(6px)' }

function caseLine(c: CaseBrief): string {
  const n = c.accounts.length
  const amt = fmtUsd(c.amount)
  switch (c.shape.typology) {
    case 'FAN-OUT': return `${amt} split out to ${n - 1} accounts`
    case 'FAN-IN': return `${amt} collected from ${n - 1} accounts`
    case 'CYCLE': return `${amt} went round a loop of ${n} accounts`
    case 'GATHER-SCATTER': return `${amt} pooled, then pushed out again`
    case 'SCATTER-GATHER': return `${amt} split across middlemen, re-collected`
    case 'STACK': return `${amt} passed through layers of accounts`
    case 'BIPARTITE': return `${amt} paid group-to-group`
    default: return `${amt} moved between ${n} accounts`
  }
}

export default function Workspace() {
  const [params, setParams] = useSearchParams()
  const batchId = params.get('batch')
  const caseId = params.get('case')

  const [samples, setSamples] = useState<string[]>([])
  const [stages, setStages] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [batch, setBatch] = useState<BatchSummary | null>(null)
  const [base, setBase] = useState<GraphLink[]>([])
  const [traced, setTraced] = useState<MapLink[]>([])
  const [pulse, setPulse] = useState<Set<string>>(new Set())
  const [steps, setSteps] = useState<LiveStep[]>([])
  const [result, setResult] = useState<CaseResult | null>(null)
  const [investigating, setInvestigating] = useState(false)

  useEffect(() => {
    getJSON<{ name: string }[]>('/api/samples').then((s) => setSamples(s.map((x) => x.name))).catch(() => undefined)
  }, [])

  const loadBatch = useCallback((id: string) => {
    getJSON<BatchSummary>(`/api/batches/${id}`).then(setBatch).catch(() => setBatch(null))
    getJSON<{ links: GraphLink[] }>(`/api/batches/${id}/graph`).then((g) => setBase(g.links)).catch(() => setBase([]))
  }, [])

  useEffect(() => {
    if (batchId) loadBatch(batchId)
    else { setBatch(null); setBase([]) }
  }, [batchId, loadBatch])

  const current = batch?.cases.find((c) => c.case_id === caseId) ?? null

  useEffect(() => {
    setTraced([]); setSteps([]); setResult(null); setPulse(new Set())
    if (caseId) getJSON<{ result: CaseResult | null }>(`/api/cases/${caseId}`).then((r) => setResult(r.result)).catch(() => undefined)
  }, [caseId])

  async function upload(opts: { sample?: string; file?: File }) {
    setError(null); setStages({}); setBusy(true)
    setParams({}, { replace: true })
    const fd = new FormData()
    if (opts.sample) fd.append('sample', opts.sample)
    if (opts.file) fd.append('file', opts.file)
    try {
      const { job_id } = await postJSON<{ job_id: string }>('/api/batches', fd)
      streamJob(job_id, (e: JobEvent) => {
        if (e.type === 'stage') setStages((s) => ({ ...s, [e.stage]: e.status }))
        if (e.type === 'error') setError(e.error)
        if (e.type === 'done') getJSON<{ batch_id: string }[]>('/api/batches').then((bs) => bs.length && setParams({ batch: bs[0].batch_id }))
      }, () => setBusy(false))
    } catch (err) { setError(String(err)); setBusy(false) }
  }

  async function investigate() {
    if (!caseId) return
    setInvestigating(true); setSteps([]); setTraced([]); setResult(null)
    try {
      const { job_id } = await postJSON<{ job_id: string }>(`/api/cases/${caseId}/investigate?policy=claude`)
      streamJob(job_id, (e: JobEvent) => {
        if (e.type === 'step_start') setSteps((s) => [...s, { step: e.step, tool: e.tool, reason: e.reason, done: false, found: [] }])
        if (e.type === 'step_done') setSteps((s) => s.map((x) => x.step === e.step
          ? { ...x, done: true, found: (e.evidence ?? []).filter((v: { kind: string }) => v.kind !== 'context').map((v: { claim: string }) => v.claim) } : x))
        if (e.type === 'trace_wave' || e.type === 'graph') {
          const add: MapLink[] = (e.edges ?? []).map((x: { tx_id: string; src: string; dst: string; amount: number }) => ({
            tx_id: x.tx_id, source: x.src, target: x.dst, amount: x.amount, kind: 'traced' as const }))
          if (add.length) {
            setTraced((t) => [...t, ...add.filter((a) => !t.some((b) => b.tx_id === a.tx_id))])
            setPulse(new Set(add.map((a) => a.tx_id)))
          }
        }
        if (e.type === 'report') setResult(e.case)
        if (e.type === 'error') setError(e.error)
      }, () => setInvestigating(false))
    } catch (err) { setError(String(err)); setInvestigating(false) }
  }

  // Map colours: case payments orange; the open case highlighted; traced money blue; red where evidence points.
  const concern = useMemo(() => new Set(result?.evidence.filter((e) => e.kind === 'support').flatMap((e) => e.tx_ids) ?? []), [result])
  const links: MapLink[] = useMemo(() => {
    const out: MapLink[] = base.map((l) => ({
      tx_id: l.tx_id, source: l.source, target: l.target, amount: l.amount,
      kind: concern.has(l.tx_id) ? 'concern' : l.case && l.case === caseId ? 'focus' : l.case ? 'flagged' : 'normal',
    }))
    const have = new Set(out.map((l) => l.tx_id))
    for (const t of traced) if (!have.has(t.tx_id)) out.push(concern.has(t.tx_id) ? { ...t, kind: 'concern' } : t)
    return out
  }, [base, traced, caseId, concern])
  const focus = useMemo(() => current?.accounts ?? [], [current])

  const d = result?.disposition
  const rep = result?.report
  const support = result?.evidence.filter((e) => e.kind === 'support') ?? []
  const counter = result?.evidence.filter((e) => e.kind === 'counter') ?? []

  return (
    <div className="relative overflow-hidden rounded-xl" style={{ height: 'calc(100vh - 80px)', border: '1px solid var(--border)' }}>
      <LiveGraph3D links={links} focus={focus} pulse={pulse} />

      {/* Empty state: just the upload */}
      {!batch && !busy && (
        <div className="absolute inset-0 flex items-center justify-center p-4">
          <div className="rounded-2xl p-8 w-full max-w-md flex flex-col items-center gap-4 text-center" style={panel}>
            <Upload size={28} style={{ color: 'var(--s1)' }} aria-hidden />
            <div className="text-lg font-semibold">Upload a day of payments</div>
            <label className="w-full px-4 py-3 rounded-lg text-sm font-medium cursor-pointer" style={{ background: 'var(--s1)', color: '#fff' }}>
              Choose CSV file
              <input type="file" accept=".csv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload({ file: f }) }} />
            </label>
            {samples.length > 0 && (
              <button onClick={() => upload({ sample: samples[0] })} className="text-sm" style={{ color: 'var(--ink-2)' }}>
                or try a sample day →
              </button>
            )}
            {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}
          </div>
        </div>
      )}

      {/* Top strip */}
      {(batch || busy) && (
        <div className="absolute top-3 left-3 flex flex-wrap items-center gap-x-8 gap-y-2 rounded-xl px-5 py-3" style={panel}>
          {batch ? (
            <>
              <Num label="Payments" value={fmtInt(batch.rows)} />
              <Num label="Accounts" value={fmtInt(batch.accounts)} />
              <Num label="Suspicious" value={fmtInt(batch.flagged_tx)} color="var(--s2)" />
              <Num label="Cases" value={fmtInt(batch.cases.length)} />
              <label className="text-xs cursor-pointer underline underline-offset-2" style={{ color: 'var(--ink-2)' }}>
                New file
                <input type="file" accept=".csv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload({ file: f }) }} />
              </label>
            </>
          ) : (
            <span className="text-sm">Checking payments…</span>
          )}
        </div>
      )}

      {/* Right panel */}
      {(batch || busy) && (
        <aside className="absolute top-3 right-3 bottom-3 w-[380px] max-w-[calc(100%-24px)] rounded-xl flex flex-col overflow-hidden" style={panel}>
          {busy && (
            <ol className="p-4 flex flex-col gap-2.5">
              {Object.keys(STAGE_PLAIN).map((k) => (
                <li key={k} className="flex items-center gap-2 text-sm" style={{ color: stages[k] ? 'var(--ink)' : 'var(--muted)' }}>
                  {stages[k] === 'done' ? <CheckCircle2 size={16} style={{ color: 'var(--good)' }} /> : stages[k]
                    ? <Loader2 size={16} className="animate-spin" style={{ color: 'var(--s1)' }} /> : <span className="w-4" />}
                  {STAGE_PLAIN[k]}
                </li>
              ))}
              {error && <li className="text-sm mt-2" style={{ color: 'var(--critical)' }}>{error}</li>}
            </ol>
          )}

          {batch && !current && !busy && (
            <>
              <div className="px-4 pt-4 pb-2 text-sm font-semibold">{batch.cases.length} cases, most suspicious first</div>
              <ul className="flex-1 overflow-y-auto px-2 pb-2">
                {batch.cases.map((c, i) => (
                  <li key={c.case_id}>
                    <button onClick={() => setParams({ batch: batch.batch_id, case: c.case_id })}
                      className="w-full text-left px-2 py-2.5 rounded-lg flex items-center gap-3 hover:brightness-125"
                      style={{ borderBottom: '1px solid var(--border)' }}>
                      <span className="text-xs tabular w-5" style={{ color: 'var(--muted)' }}>{i + 1}</span>
                      <span className="flex-1 min-w-0">
                        <span className="block text-sm truncate">{caseLine(c)}</span>
                        <span className="block text-xs" style={{ color: 'var(--muted)' }}>{trickName(c.shape.typology)}</span>
                      </span>
                      {c.result ? <ActionBadge action={c.result.action} /> : <Risk p={c.ml_score} />}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          {current && (
            <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
              <button onClick={() => setParams({ batch: batchId! })} className="flex items-center gap-1 text-xs self-start" style={{ color: 'var(--ink-2)' }}>
                <ArrowLeft size={14} /> All cases
              </button>
              <div>
                <div className="text-base font-semibold">{caseLine(current)}</div>
                <div className="text-xs" style={{ color: 'var(--muted)' }}>Looks like: {trickName(current.shape.typology)}</div>
              </div>

              {!result && !investigating && (
                <button onClick={investigate} className="px-4 py-2.5 rounded-lg text-sm font-medium" style={{ background: 'var(--s1)', color: '#fff' }}>
                  Investigate this case
                </button>
              )}

              {investigating && (
                <ol className="flex flex-col gap-3">
                  {steps.map((s) => (
                    <li key={s.step} className="text-sm">
                      <div className="flex items-center gap-2">
                        {s.done ? <CheckCircle2 size={14} style={{ color: 'var(--good)' }} /> : <Loader2 size={14} className="animate-spin" style={{ color: 'var(--s1)' }} />}
                        <span className="font-medium">{STEP_PLAIN[s.tool]?.label ?? s.tool}</span>
                      </div>
                      {s.found.map((f, i) => <p key={i} className="text-xs mt-1 ml-6" style={{ color: 'var(--ink-2)' }}>{f}</p>)}
                    </li>
                  ))}
                  {steps.length > 0 && steps.every((s) => s.done) && (
                    <li className="text-sm flex items-center gap-2" style={{ color: 'var(--ink-2)' }}>
                      <Loader2 size={14} className="animate-spin" /> Writing the report…
                    </li>
                  )}
                  {error && <li className="text-sm" style={{ color: 'var(--critical)' }}>{error}</li>}
                </ol>
              )}

              {result && d && rep && (
                <>
                  <div className="rounded-lg p-3 flex flex-col gap-2" style={{ border: '1px solid var(--border)' }}>
                    <ActionBadge action={d.action} large />
                    <p className="text-sm leading-relaxed">{rep.executive_summary.text}</p>
                  </div>
                  {support.length > 0 && (
                    <Reasons title="Why it is suspicious" color="var(--critical)" items={support.map((e) => e.claim)} />
                  )}
                  {counter.length > 0 && (
                    <Reasons title="Why it may be normal" color="var(--good)" items={counter.map((e) => e.claim)} />
                  )}
                  {rep.next_steps.length > 0 && <Reasons title="What to do next" color="var(--s1)" items={rep.next_steps} />}
                  {rep.str_narrative && (
                    <details className="text-sm">
                      <summary className="cursor-pointer font-medium">Draft report for FIU-IND (STR)</summary>
                      <p className="mt-2 text-xs leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--ink-2)' }}>{rep.str_narrative}</p>
                    </details>
                  )}
                  <button onClick={investigate} className="text-xs self-start underline underline-offset-2" style={{ color: 'var(--muted)' }}>Run again</button>
                </>
              )}
            </div>
          )}
        </aside>
      )}
    </div>
  )
}

function Num({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>{label}</span>
      <span className="text-xl font-semibold tabular" style={{ color: color ?? 'var(--ink)' }}>{value}</span>
    </div>
  )
}

function Risk({ p }: { p: number }) {
  const label = p >= 0.7 ? 'High' : p >= 0.4 ? 'Medium' : 'Low'
  return <span className="text-xs px-2 py-0.5 rounded-full" style={{ border: '1px solid var(--border)', color: 'var(--ink-2)' }}>{label}</span>
}

function Reasons({ title, color, items }: { title: string; color: string; items: string[] }) {
  return (
    <div>
      <div className="flex items-center gap-2 text-sm font-semibold mb-1.5">
        <span className="w-2 h-2 rounded-full" style={{ background: color }} aria-hidden />{title}
      </div>
      <ul className="flex flex-col gap-1.5">
        {items.map((t, i) => <li key={i} className="text-sm" style={{ color: 'var(--ink-2)' }}>{t}</li>)}
      </ul>
    </div>
  )
}
