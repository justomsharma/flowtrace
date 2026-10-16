import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, Loader2, Upload } from 'lucide-react'
import LiveGraph3D, { type MapLink } from '../components/LiveGraph3D'
import { ActionBadge } from '../components/badges'
import CaseGlance from '../components/CaseGlance'
import {
  fmtInt, fmtUsd, getJSON, postJSON, streamJob, type Action, type BatchSummary, type CaseBrief, type CaseResult,
  type HoldPlan, type JobBrief, type JobEvent,
} from '../lib/api'
import { STAGE_PLAIN, caseReason, pct, shortAcct, trickName } from '../lib/plain'

interface GraphLink { tx_id: string; source: string; target: string; amount: number; p: number; case: string | null }
interface LiveStep { step: number; tool: string; reason: string; done: boolean; found: string[] }

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

const panel = { background: 'color-mix(in srgb, var(--surface) 88%, transparent)', border: '1px solid var(--border)', backdropFilter: 'blur(6px)' }

/** Today's work = within the team's daily capacity. Batches saved before tiers existed fall back to rank. */
const inToday = (c: CaseBrief, i: number, capacity?: number) => (c.tier ? c.tier === 'today' : i < (capacity ?? 15))

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
  const navigate = useNavigate()
  const openCase = (batchId: string, caseId: string) => navigate({ pathname: '/case', search: `?batch=${batchId}&case=${caseId}` })
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
  const [, setSteps] = useState<LiveStep[]>([])
  const [result, setResult] = useState<CaseResult | null>(null)
  const [investigating, setInvestigating] = useState(false)
  const [, setJobStatus] = useState<JobBrief['status'] | null>(null)
  // The hold plan: accounts proposed for a hold (or held) are marked on the map, with the trail payments into them.
  const [holdPlan, setHoldPlan] = useState<{ plan: HoldPlan | null; actions: Action[] }>({ plan: null, actions: [] })
  const [, setPlanVersion] = useState(0)
  const stopStream = useRef<(() => void) | null>(null)
  const following = useRef<string | null>(null)  // the job the panel is streaming, so it is never followed twice
  const caseRef = useRef(caseId)
  caseRef.current = caseId
  // Live build animation while a file is being processed.
  const [live, setLive] = useState<MapLink[]>([])
  const [liveKind, setLiveKind] = useState<Record<string, MapLink['kind']>>({})
  const [caption, setCaption] = useState('')
  const queue = useRef<Promise<void>>(Promise.resolve())
  const enqueue = (fn: () => Promise<void>) => { queue.current = queue.current.then(fn).catch(() => undefined) }

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

  // While the top cases are being investigated in the background, keep the list and progress fresh.
  const pf = batch?.prefetch
  const working = !!batch && ((!!pf && pf.done + pf.failed < pf.total) || batch.cases.some((c) => c.job))
  useEffect(() => {
    if (!working || !batchId) return
    let live = true  // a reply that lands after the batch changed (or a new upload started) is dropped
    // An interval, not a timeout chained on each reply: a failed fetch must not stop the polling.
    const t = setInterval(() => getJSON<BatchSummary>(`/api/batches/${batchId}`).then((b) => { if (live) setBatch(b) }).catch(() => undefined), 3000)
    return () => { live = false; clearInterval(t) }
  }, [working, batchId])

  const current = batch?.cases.find((c) => c.case_id === caseId) ?? null
  // Cases arrive ranked; the top `capacity` are today's work, the rest stay listed (folded) below.
  const today = useMemo(() => batch?.cases.filter((c, i) => inToday(c, i, batch.capacity)) ?? [], [batch])
  const lower = useMemo(() => batch?.cases.filter((c, i) => !inToday(c, i, batch.capacity)) ?? [], [batch])
  const [showLower, setShowLower] = useState(false)
  const marked = useMemo(() => holdPlan.actions.filter((a) => a.type === 'HOLD' && a.account && (a.status === 'PROPOSED' || a.status === 'APPROVED'))
    .map((a) => a.account as string), [holdPlan])

  // Opening a case shows its finished result, or joins its background investigation (replayed, then live).
  useEffect(() => {
    let stale = false
    stopStream.current?.(); stopStream.current = null; following.current = null
    setTraced([]); setSteps([]); setResult(null); setPulse(new Set()); setInvestigating(false); setJobStatus(null); setError(null)
    setHoldPlan({ plan: null, actions: [] })
    if (caseId) {
      getJSON<{ result: CaseResult | null; job: JobBrief | null }>(`/api/cases/${caseId}`).then((r) => {
        if (stale) return
        if (r.result) setResult(r.result)
        else if (r.job) { setJobStatus(r.job.status); follow(r.job.id) }
      }).catch(() => undefined)
    }
    return () => { stale = true; stopStream.current?.(); stopStream.current = null; following.current = null }
  }, [caseId])

  // A case opened before its background job existed (or whose run was replaced): join the job once the list shows it.
  const currentJob = current?.job?.id
  useEffect(() => {
    if (current?.job && !result && !investigating) { setJobStatus(current.job.status); follow(current.job.id) }
  }, [currentJob])

  async function upload(opts: { sample?: string; file?: File }) {
    setError(null); setStages({}); setBusy(true)
    setLive([]); setLiveKind({}); setBase([]); setBatch(null); setCaption('Reading the file…')
    queue.current = Promise.resolve()
    setParams({}, { replace: true })
    const fd = new FormData()
    if (opts.sample) fd.append('sample', opts.sample)
    if (opts.file) fd.append('file', opts.file)
    try {
      const { job_id } = await postJSON<{ job_id: string }>('/api/batches', fd)
      let thisBatch: string | null = null  // announced by the job itself; never guessed from the batch list
      streamJob(job_id, (e: JobEvent) => {
        if (e.type === 'batch') thisBatch = e.batch_id
        if (e.type === 'stage') setStages((s) => ({ ...s, [e.stage]: e.status }))
        if (e.type === 'graph_rows') {
          const rows: MapLink[] = e.links.map((l: MapLink) => ({ ...l, kind: 'normal' as const }))
          enqueue(async () => {
            setCaption(`Drawing ${fmtInt(rows.length)} payments…`)
            const step = Math.max(30, Math.ceil(rows.length / 28))
            for (let i = 0; i < rows.length; i += step) {
              setLive((prev) => [...prev, ...rows.slice(i, i + step)])
              await sleep(170)
            }
          })
        }
        if (e.type === 'scores') {
          const ids: string[] = e.unusual.map((u: { tx_id: string }) => u.tx_id)
          enqueue(async () => {
            setCaption(`${fmtInt(ids.length)} payments look unusual`)
            const step = Math.max(6, Math.ceil(ids.length / 14))
            for (let i = 0; i < ids.length; i += step) {
              const chunk = ids.slice(i, i + step)
              setLiveKind((k) => ({ ...k, ...Object.fromEntries(chunk.map((t) => [t, 'flagged' as const])) }))
              setPulse(new Set(chunk))
              await sleep(220)
            }
          })
        }
        if (e.type === 'hits') {
          const hits: { detector: string; account: string; tx_ids: string[] }[] = e.hits
          enqueue(async () => {
            for (const h of hits.slice(0, 24)) {
              setCaption(`${trickName(h.detector)} found at account ${shortAcct(h.account)}`)
              setLiveKind((k) => ({ ...k, ...Object.fromEntries(h.tx_ids.map((t) => [t, 'concern' as const])) }))
              setPulse(new Set(h.tx_ids))
              await sleep(650)
            }
          })
        }
        if (e.type === 'error') {
          const msg = e.error
          enqueue(async () => { setCaption(''); setError(msg) })
        }
        if (e.type === 'done') {
          enqueue(async () => {
            setCaption('')
            if (thisBatch) setParams({ batch: thisBatch })
          })
        }
      }, () => enqueue(async () => setBusy(false)))
    } catch (err) { setError(String(err)); setBusy(false) }
  }

  const refreshBatch = () => {
    const id = batchId
    if (id) getJSON<BatchSummary>(`/api/batches/${id}`).then((b) => { if (b.batch_id === id) setBatch(b) }).catch(() => undefined)
  }

  /** Stream a case job into the panel. Events are replayed from the start, so joining late loses nothing. */
  function follow(jobId: string) {
    if (following.current === jobId) return  // already streaming it (the case-open fetch and the list can both see it)
    stopStream.current?.()
    following.current = jobId
    setInvestigating(true); setSteps([]); setTraced([]); setResult(null); setError(null)
    stopStream.current = streamJob(jobId, (e: JobEvent) => {
        if (e.type === 'step_start') {
          setJobStatus('running')
          setSteps((s) => [...s, { step: e.step, tool: e.tool, reason: e.reason, done: false, found: [] }])
        }
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
        if (e.type === 'cancelled') {
          setResult(null)  // a report from a stopped run is not this case's result
          setError(e.reason === 'shutdown'
            ? 'The server stopped, so this run was stopped. It is investigated again when the server restarts.'
            : 'This file was uploaded again, so this run was stopped.')
        }
      }, () => { setInvestigating(false); stopStream.current = null; following.current = null; refreshBatch(); setPlanVersion((v) => v + 1) })
  }

  // Map colours: case payments orange; the open case highlighted; traced money blue; red where evidence points.
  const concern = useMemo(() => new Set(result?.evidence.filter((e) => e.kind === 'support').flatMap((e) => e.tx_ids) ?? []), [result])
  const links: MapLink[] = useMemo(() => {
    if (!base.length) return live.map((l) => ({ ...l, kind: liveKind[l.tx_id] ?? l.kind }))
    const out: MapLink[] = base.map((l) => ({
      tx_id: l.tx_id, source: l.source, target: l.target, amount: l.amount,
      kind: concern.has(l.tx_id) ? 'concern' : l.case && l.case === caseId ? 'focus' : l.case ? 'flagged' : 'normal',
    }))
    const have = new Set(out.map((l) => l.tx_id))
    for (const t of traced) if (!have.has(t.tx_id)) out.push(concern.has(t.tx_id) ? { ...t, kind: 'concern' } : t)
    // Payments that carried case money into an account proposed for a hold.
    const into = new Set(marked)
    for (const e of holdPlan.plan?.network.edges ?? []) {
      if (e.kind !== 'trail' || !into.has(e.dst)) continue
      const i = out.findIndex((l) => l.tx_id === e.tx_id)
      const link: MapLink = { tx_id: e.tx_id, source: e.src, target: e.dst, amount: e.amount, kind: 'hold' }
      if (i >= 0) out[i] = link
      else out.push(link)
    }
    return out
  }, [base, traced, caseId, concern, live, liveKind, holdPlan, marked])
  const focus = useMemo(() => [...(current?.accounts ?? []), ...marked.filter((a) => !current?.accounts.includes(a))], [current, marked])


  return (
    <div className="relative overflow-hidden rounded-xl" style={{ height: 'calc(100vh - 24px)', border: '1px solid var(--border)' }}>
      <LiveGraph3D links={links} focus={focus} pulse={pulse} marked={marked} />

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

      {caption && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 px-5 py-2.5 rounded-full text-sm font-medium flex items-center gap-2" style={panel} aria-live="polite">
          <Loader2 size={14} className="animate-spin" style={{ color: 'var(--s2)' }} />{caption}
        </div>
      )}

      {/* Top strip */}
      {(batch || busy) && (
        <div className="absolute top-3 left-3 flex flex-wrap items-center gap-x-8 gap-y-2 rounded-xl px-5 py-3 md:max-w-[calc(100%-416px)]" style={panel}>
          {batch ? (
            <>
              <Funnel steps={[
                [batch.rows, 'payments checked'],
                [batch.flagged_tx, `flagged (${batch.rows ? Math.round((100 * batch.flagged_tx) / batch.rows) : 0}%)`],
                [batch.cases.length, batch.cases.length === 1 ? 'case for analysts' : 'cases for analysts'],
              ]} />
              <span className="flex items-center gap-1.5 text-sm tabular" style={{ color: 'var(--good)' }}>
                <CheckCircle2 size={14} aria-hidden />{fmtInt(batch.rows - batch.flagged_tx)} passed as normal
              </span>
              {today.length < batch.cases.length && (
                <span className="text-sm tabular" style={{ color: 'var(--ink-2)' }}>{today.length} in today's queue (capacity)</span>
              )}
              {batch.prefetch && batch.prefetch.total > 0 && (
                <span className="flex items-center gap-1.5 text-sm tabular" style={{ color: 'var(--ink-2)' }} aria-live="polite">
                  {batch.prefetch.done + batch.prefetch.failed < batch.prefetch.total
                    ? <Loader2 size={14} className="animate-spin" style={{ color: 'var(--s1)' }} aria-hidden />
                    : <CheckCircle2 size={14} style={{ color: 'var(--good)' }} aria-hidden />}
                  {batch.prefetch.done} of {batch.prefetch.total} investigated
                  {batch.prefetch.failed > 0 && <span style={{ color: 'var(--critical)' }}>· {batch.prefetch.failed} failed</span>}
                </span>
              )}
              <button onClick={() => navigate('/?fresh=1')} className="text-xs underline underline-offset-2" style={{ color: 'var(--ink-2)' }}
                title="Clear the screen and start with a new upload. Past uploads stay saved in Today's work.">
                Start fresh
              </button>
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
        <aside className={`absolute top-3 right-3 w-[380px] max-w-[calc(100%-24px)] rounded-xl flex flex-col overflow-hidden ${busy ? 'max-h-[calc(100%-24px)]' : 'bottom-3'}`} style={panel}>
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
            <div className="flex-1 overflow-y-auto pb-2">
              <div className="px-4 pt-4 pb-1">
                <h2 className="text-sm font-semibold">Today's work</h2>
                <p className="text-xs mt-1 flex items-center gap-1.5 tabular" style={{ color: 'var(--good)' }}>
                  <CheckCircle2 size={13} aria-hidden />{fmtInt(batch.rows - batch.flagged_tx)} of {fmtInt(batch.rows)} payments passed as normal: no action needed
                </p>
                <p className="text-xs mt-0.5" style={{ color: 'var(--muted)' }}>
                  {batch.cases.length === 0
                    ? 'No payment in this file reached the review bar.'
                    : `${today.length} of ${batch.cases.length} cases, highest chance of laundering first`}
                </p>
              </div>
              <ul className="px-2">
                {today.map((c, i) => <CaseRow key={c.case_id} c={c} rank={i + 1} onOpen={() => openCase(batch.batch_id, c.case_id)} />)}
              </ul>
              {lower.length > 0 && (
                <>
                  <button onClick={() => setShowLower((v) => !v)} aria-expanded={showLower}
                    className="mx-4 mt-4 mb-1 flex items-center gap-1.5 text-sm font-semibold" style={{ color: 'var(--ink-2)' }}>
                    {showLower ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
                    Lower priority <span className="font-normal tabular" style={{ color: 'var(--muted)' }}>{lower.length}</span>
                  </button>
                  {showLower && (
                    <ul className="px-2">
                      {lower.map((c, i) => <CaseRow key={c.case_id} c={c} rank={today.length + i + 1} quiet
                        onOpen={() => openCase(batch.batch_id, c.case_id)} />)}
                    </ul>
                  )}
                </>
              )}
            </div>
          )}

          {current && (
            <div className="p-4 flex flex-col gap-3">
              <button onClick={() => setParams({ batch: batchId! })} className="flex items-center gap-1 text-xs self-start" style={{ color: 'var(--ink-2)' }}>
                <ArrowLeft size={14} /> All cases
              </button>
              <div>
                <div className="text-base font-semibold">{caseLine(current)}</div>
                <div className="text-xs" style={{ color: 'var(--muted)' }}>Looks like: {trickName(current.shape.typology)}</div>
              </div>
              {current.result && <ActionBadge action={current.result.action} />}
              {current.result && <CaseGlance caseId={current.case_id} version={current.result.action} />}
              <button onClick={() => openCase(batch!.batch_id, current.case_id)} className="px-4 py-2.5 rounded-lg text-sm font-medium"
                style={{ background: 'var(--s1)', color: '#fff' }}>
                Open the case report
              </button>
            </div>
          )}
        </aside>
      )}
    </div>
  )
}

/** The day narrowed down, read left to right: payments, flagged, cases, and what the team works today. */
function Funnel({ steps }: { steps: [number, string][] }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1" aria-label={steps.map(([n, l]) => `${n} ${l}`).join(', ')}>
      {steps.map(([n, label], i) => (
        <span key={label} className="flex items-baseline gap-2">
          {i > 0 && <ChevronRight size={14} className="self-center" style={{ color: 'var(--muted)' }} aria-hidden />}
          <span className="text-xl font-semibold tabular" style={{ color: i === steps.length - 1 ? 'var(--s2)' : 'var(--ink)' }}>{fmtInt(n)}</span>
          <span className="text-sm" style={{ color: 'var(--ink-2)' }}>{label}</span>
        </span>
      ))}
    </p>
  )
}

function CaseRow({ c, rank, quiet = false, onOpen }: { c: CaseBrief; rank: number; quiet?: boolean; onOpen: () => void }) {
  return (
    <li>
      <button onClick={onOpen} className="w-full text-left px-2 py-2.5 rounded-lg flex items-center gap-3 hover:brightness-125"
        style={{ borderBottom: '1px solid var(--border)', opacity: quiet ? 0.75 : 1 }}>
        <span className="text-xs tabular w-6 self-stretch flex items-center pl-1.5"
          style={{ color: 'var(--muted)', borderLeft: quiet ? '2px solid transparent' : '2px solid var(--s2)' }}>{rank}</span>
        <span className="flex-1 min-w-0">
          <span className="block text-sm truncate">{caseLine(c)}</span>
          <span className="block text-xs truncate" style={{ color: 'var(--muted)' }}>{caseReason(c.signals ?? [])}</span>
        </span>
        {c.result ? <ActionBadge action={c.result.action} />
          : c.failed ? <span className="text-xs" style={{ color: 'var(--critical)' }}>Failed</span>
          : c.job ? (
            <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--ink-2)' }}>
              {c.job.status === 'running' && <Loader2 size={12} className="animate-spin" aria-hidden />}
              {c.job.status === 'running' ? 'Investigating' : 'Queued'}
            </span>
          )
          : <span className="text-sm font-semibold tabular"
              title="Chance that at least one signal is right (model and detectors treated as independent)"
              style={{ color: quiet ? 'var(--ink-2)' : 'var(--ink)' }}>{pct(c.case_score ?? c.ml_score)}</span>}
      </button>
    </li>
  )
}
