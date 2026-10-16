import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, Orbit, Upload } from 'lucide-react'
import { ActionBadge } from '../components/badges'
import { fmtInt, getJSON, postJSON, streamJob, type CaseBrief, type JobEvent } from '../lib/api'
import { useBatch } from '../lib/batch'
import { fmtMoney } from '../lib/money'
import { SAMPLE_INFO, STAGE_PLAIN, caseReason, caseSentence, inToday, pct, trickName, caseNo } from '../lib/plain'


export default function Today() {
  const { batch, batchId, caseId, ready, query, refresh } = useBatch()
  const navigate = useNavigate()
  const path = useLocation().pathname
  const here = useRef(true)  // still on Today when an upload finishes? Then open its batch here.
  useEffect(() => { here.current = true; return () => { here.current = false } }, [])
  // Links from before the case page existed (/?batch=..&case=..) open the case.
  useEffect(() => { if (caseId && path === '/') navigate({ pathname: '/case', search: query(caseId) }, { replace: true }) }, [caseId, path, navigate, query])
  const [samples, setSamples] = useState<string[]>([])
  const [stages, setStages] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showLower, setShowLower] = useState(false)

  useEffect(() => {
    getJSON<{ name: string }[]>('/api/samples').then((s) => setSamples(s.map((x) => x.name))).catch(() => undefined)
  }, [])

  const today = useMemo(() => batch?.cases.filter((c, i) => inToday(c, i, batch.capacity)) ?? [], [batch])
  const lower = useMemo(() => batch?.cases.filter((c, i) => !inToday(c, i, batch.capacity)) ?? [], [batch])
  const sample = samples.includes('demo_mixed_2022-09-09.csv') ? 'demo_mixed_2022-09-09.csv' : samples[0]

  async function upload(opts: { sample?: string; file?: File }) {
    setError(null); setStages({}); setBusy(true)
    const fd = new FormData()
    if (opts.sample) fd.append('sample', opts.sample)
    if (opts.file) fd.append('file', opts.file)
    try {
      const { job_id } = await postJSON<{ job_id: string }>('/api/batches', fd)
      let id: string | null = null  // announced by the job itself
      streamJob(job_id, (e: JobEvent) => {
        if (e.type === 'batch') id = e.batch_id
        if (e.type === 'stage') setStages((s) => ({ ...s, [e.stage]: e.status }))
        if (e.type === 'error') setError(e.error)
        if (e.type === 'done' && id) {
          if (id === batchId) refresh()
          else if (here.current) navigate({ pathname: '/today', search: `?batch=${id}` })
        }
      }, () => setBusy(false))
    } catch (err) { setError(String(err)); setBusy(false) }
  }

  const uploadControls = (
    <div className="flex flex-wrap items-center gap-2">
      <label className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium cursor-pointer focus-within:outline-2 focus-within:outline-offset-2"
        style={{ background: batch ? 'var(--surface)' : 'var(--brand)', color: batch ? 'var(--ink)' : 'var(--brand-ink)', border: `1px solid ${batch ? 'var(--border)' : 'var(--brand)'}`, outlineColor: 'var(--focus)' }}>
        <Upload size={16} aria-hidden /> {batch ? 'Upload another day' : 'Upload a CSV of payments'}
        <input type="file" accept=".csv" className="sr-only" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload({ file: f }); e.target.value = '' }} />
      </label>
      {sample && (
        <button onClick={() => upload({ sample })} disabled={busy}
          className="px-3.5 py-2 rounded-lg text-sm font-medium disabled:opacity-60"
          style={{ border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--ink)' }}>
          {batch?.filename === sample ? 'Upload the sample day again' : 'Try the sample day'}
        </button>
      )}
    </div>
  )

  return (
    <div className="px-6 py-5 flex flex-col gap-5 max-w-[1240px]">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Today's work</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--ink-2)' }}>
            {batch ? <>Cases from <span className="font-medium">{batch.filename}</span>, highest chance of laundering first.</>
              : 'Upload a day of payments. FlowTrace scores every payment, groups the suspicious ones into cases and investigates the top ones.'}
          </p>
        </div>
        {uploadControls}
      </header>

      {busy && (
        <section className="card p-4" aria-live="polite">
          <h2 className="text-sm font-semibold mb-2">Checking the payments</h2>
          <ol className="flex flex-wrap gap-x-6 gap-y-2">
            {Object.keys(STAGE_PLAIN).map((k) => (
              <li key={k} className="flex items-center gap-2 text-sm" style={{ color: stages[k] ? 'var(--ink)' : 'var(--muted)' }}>
                {stages[k] === 'done' ? <CheckCircle2 size={16} style={{ color: 'var(--good)' }} aria-hidden />
                  : stages[k] ? <Loader2 size={16} className="animate-spin" style={{ color: 'var(--brand)' }} aria-hidden />
                  : <span className="w-4 h-4 rounded-full" style={{ border: '1.5px solid var(--axis)' }} aria-hidden />}
                {STAGE_PLAIN[k]}
              </li>
            ))}
          </ol>
          <p className="text-xs mt-3" style={{ color: 'var(--muted)' }}>
            To watch the payments being drawn as they load, upload from the <Link to="/" className="underline">Live Network</Link> instead.
          </p>
        </section>
      )}
      {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}

      {!batch && ready && !busy && sample && SAMPLE_INFO[sample] && (
        <section className="card p-5 max-w-xl">
          <h2 className="text-base font-semibold">{SAMPLE_INFO[sample].title}</h2>
          <p className="text-sm mt-1" style={{ color: 'var(--ink-2)' }}>{SAMPLE_INFO[sample].body}</p>
        </section>
      )}

      {batch && (
        <>
          <section aria-label="How the day narrowed down" className="card px-5 py-4 flex flex-wrap items-center gap-x-8 gap-y-3">
            <Funnel steps={[[batch.rows, 'payments checked'], [batch.flagged_tx, `flagged (${batch.rows ? Math.round((100 * batch.flagged_tx) / batch.rows) : 0}%)`],
              [batch.cases.length, batch.cases.length === 1 ? 'case for analysts' : 'cases for analysts']]} />
            <span className="flex items-center gap-1.5 text-sm tabular font-medium" style={{ color: 'var(--good)' }}>
              <CheckCircle2 size={15} aria-hidden />{(batch.rows - batch.flagged_tx).toLocaleString('en-US')} passed as normal
            </span>
            {today.length < batch.cases.length && (
              <span className="text-sm tabular" style={{ color: 'var(--ink-2)' }}>{today.length} in today's queue (capacity)</span>
            )}
            {batch.prefetch && batch.prefetch.total > 0 && (
              <span className="flex items-center gap-1.5 text-sm tabular" style={{ color: 'var(--ink-2)' }} aria-live="polite">
                {batch.prefetch.done + batch.prefetch.failed < batch.prefetch.total
                  ? <Loader2 size={14} className="animate-spin" style={{ color: 'var(--brand)' }} aria-hidden />
                  : <CheckCircle2 size={14} style={{ color: 'var(--good)' }} aria-hidden />}
                {batch.prefetch.done} of {batch.prefetch.total} investigated by the agent
                {batch.prefetch.failed > 0 && <span style={{ color: 'var(--critical)' }}>, {batch.prefetch.failed} failed</span>}
              </span>
            )}
            <Link to={{ pathname: '/', search: query(null) }} className="ml-auto inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: 'var(--brand)' }}>
              <Orbit size={16} aria-hidden /> See the day on the Live Network
            </Link>
          </section>

          {batch.cases.length === 0 ? (
            <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No payment in this file reached the review bar.</p>
          ) : (
            <section className="card overflow-hidden">
              <CaseTable cases={today} start={1} query={query} />
              {lower.length > 0 && (
                <div style={{ borderTop: '1px solid var(--border)' }}>
                  <button onClick={() => setShowLower((v) => !v)} aria-expanded={showLower}
                    className="w-full px-5 py-3 flex items-center gap-1.5 text-sm font-semibold text-left" style={{ color: 'var(--ink-2)' }}>
                    {showLower ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
                    Lower priority <span className="font-normal tabular" style={{ color: 'var(--muted)' }}>{lower.length} beyond today's capacity</span>
                  </button>
                  {showLower && <CaseTable cases={lower} start={today.length + 1} query={query} quiet />}
                </div>
              )}
            </section>
          )}
        </>
      )}
    </div>
  )
}

function Funnel({ steps }: { steps: [number, string][] }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
      {steps.map(([n, label], i) => (
        <span key={label} className="flex items-baseline gap-2">
          {i > 0 && <ChevronRight size={14} className="self-center" style={{ color: 'var(--muted)' }} aria-hidden />}
          <span className="text-xl font-semibold tabular" style={{ color: i === steps.length - 1 ? 'var(--brand)' : 'var(--ink)' }}>{fmtInt(n)}</span>
          <span className="text-sm" style={{ color: 'var(--ink-2)' }}>{label}</span>
        </span>
      ))}
    </p>
  )
}

function CaseTable({ cases, start, query, quiet = false }: { cases: CaseBrief[]; start: number; query: (c?: string | null) => string; quiet?: boolean }) {
  const navigate = useNavigate()
  return (
    <table className="w-full text-sm">
      <thead className={quiet ? 'sr-only' : ''}>
        <tr className="text-left text-xs" style={{ color: 'var(--muted)', background: 'var(--surface-2)' }}>
          <th className="py-2 pl-5 pr-3 font-medium w-12">Rank</th>
          <th className="pr-3 font-medium">Case</th>
          <th className="pr-3 font-medium">What the money did</th>
          <th className="pr-3 font-medium">Why it is on the list</th>
          <th className="pr-3 font-medium text-right" title="Chance that at least one signal is right">Score</th>
          <th className="pr-5 font-medium">Investigation</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((c, i) => (
          <tr key={c.case_id} onClick={() => navigate({ pathname: '/case', search: query(c.case_id) })}
            className="cursor-pointer hover:bg-[var(--surface-2)]" style={{ borderTop: '1px solid var(--border)', opacity: quiet ? 0.8 : 1 }}>
            <td className="py-3 pl-5 pr-3 tabular" style={{ color: 'var(--muted)' }}>{start + i}</td>
            <td className="pr-3 font-semibold tabular">
              <Link to={{ pathname: '/case', search: query(c.case_id) }} onClick={(e) => e.stopPropagation()}>Case {caseNo(c.case_id)}</Link>
            </td>
            <td className="pr-3">
              <span className="block">{caseSentence(c.shape.typology, fmtMoney(c.amount, c.currency, c.rate), c.accounts.length)}</span>
              <span className="block text-xs" style={{ color: 'var(--muted)' }}>Looks like: {trickName(c.shape.typology)}</span>
            </td>
            <td className="pr-3 text-xs" style={{ color: 'var(--ink-2)' }}>{caseReason(c.signals ?? [])}</td>
            <td className="pr-3 text-right font-semibold tabular">{pct(c.case_score ?? c.ml_score)}</td>
            <td className="pr-5">
              {c.job ? (
                <span className="inline-flex items-center gap-1.5 text-xs" style={{ color: 'var(--ink-2)' }}>
                  {c.job.status === 'running' && <Loader2 size={12} className="animate-spin" aria-hidden />}
                  {c.job.status === 'running' ? 'Investigating' : 'Queued'}
                </span>
              ) : c.result ? <ActionBadge action={c.result.action} />
                : c.failed ? <span className="text-xs" style={{ color: 'var(--critical)' }}>Failed: open to run again</span>
                : <span className="text-xs" style={{ color: 'var(--muted)' }}>Not investigated</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
