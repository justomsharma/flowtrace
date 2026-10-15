import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { FileUp, Play } from 'lucide-react'
import { ChartCard, StatTile, TipBox, axisProps } from '../components/ui'
import { ActionBadge } from '../components/badges'
import {
  fmtInt, fmtPct, fmtUsd, getJSON, postJSON, streamJob, type BatchSummary, type JobEvent,
} from '../lib/api'

const STAGES = [
  { key: 'parse', label: 'Parse & validate' },
  { key: 'score', label: 'Score with model' },
  { key: 'graph', label: 'Load into FalkorDB' },
  { key: 'detect', label: 'Run graph detectors' },
  { key: 'cases', label: 'Build cases' },
]

type StageState = Record<string, { status: string; info: string }>

function stageInfo(e: JobEvent): string {
  switch (e.stage) {
    case 'parse': return e.rows ? `${fmtInt(e.rows)} rows, ${fmtInt(e.accounts)} accounts` : ''
    case 'score': return e.above_candidate !== undefined ? `${fmtInt(e.above_candidate)} transfers above candidate score` : ''
    case 'graph': return e.total ? `${fmtInt(e.done ?? e.rows ?? 0)} / ${fmtInt(e.total ?? e.rows)} transfers` : e.rows ? `${fmtInt(e.rows)} transfers` : ''
    case 'detect': return e.hits !== undefined ? `${e.hits} detector hits` : ''
    case 'cases': return e.cases !== undefined ? `${e.cases} cases` : ''
  }
  return ''
}

export default function Investigate() {
  const [params, setParams] = useSearchParams()
  const [samples, setSamples] = useState<{ name: string; bytes: number }[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [stages, setStages] = useState<StageState>({})
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [batch, setBatch] = useState<BatchSummary | null>(null)
  const batchId = params.get('batch')

  useEffect(() => {
    getJSON<{ name: string; bytes: number }[]>('/api/samples').then(setSamples).catch(() => setSamples([]))
  }, [])

  useEffect(() => {
    if (batchId) getJSON<BatchSummary>(`/api/batches/${batchId}`).then(setBatch).catch(() => setBatch(null))
    else
      getJSON<{ batch_id: string }[]>('/api/batches').then((bs) => {
        if (bs.length) setParams({ batch: bs[0].batch_id }, { replace: true })
      }).catch(() => undefined)
  }, [batchId, setParams])

  async function run(sample?: string) {
    setError(null)
    setStages({})
    setRunning(true)
    const fd = new FormData()
    if (sample) fd.append('sample', sample)
    else if (file) fd.append('file', file)
    try {
      const { job_id } = await postJSON<{ job_id: string }>('/api/batches', fd)
      streamJob(job_id, (e) => {
        if (e.type === 'stage') setStages((s) => ({ ...s, [e.stage]: { status: e.status, info: stageInfo(e) || s[e.stage]?.info || '' } }))
        if (e.type === 'error') setError(e.error)
        if (e.type === 'done') {
          getJSON<{ events: number }>(`/api/jobs/${job_id}`).then(() =>
            getJSON<{ batch_id: string }[]>('/api/batches').then((bs) => bs.length && setParams({ batch: bs[0].batch_id })),
          )
        }
      }, () => setRunning(false))
    } catch (e) {
      setError(String(e))
      setRunning(false)
    }
  }

  const hist = (batch?.score_histogram ?? []).map((n, i) => ({ bin: `${(i * 0.05).toFixed(2)}`, n }))
  const investigated = batch?.cases.filter((c) => c.result) ?? []

  return (
    <div className="flex flex-col gap-4">
      <ChartCard title="Screen a batch of transactions"
        subtitle="IBM AML CSV format. Labels, if present, are kept aside for scoring and never shown to the agent.">
        <div className="flex flex-col md:flex-row gap-3 md:items-center">
          <label className="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm"
            style={{ border: '1px dashed var(--axis)', color: 'var(--ink-2)' }}>
            <FileUp size={16} aria-hidden />
            <span className="truncate max-w-[260px]">{file ? file.name : 'Choose CSV…'}</span>
            <input type="file" accept=".csv" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <button disabled={!file || running} onClick={() => run()}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-40"
            style={{ background: 'var(--s1)', color: '#fff' }}>
            <Play size={14} aria-hidden /> Run pipeline
          </button>
          <span className="text-xs" style={{ color: 'var(--muted)' }}>or try a sample:</span>
          <div className="flex flex-wrap gap-2">
            {samples.map((s) => (
              <button key={s.name} disabled={running} onClick={() => run(s.name)}
                className="px-2.5 py-1.5 rounded-md text-xs disabled:opacity-40"
                style={{ border: '1px solid var(--border)', color: 'var(--ink-2)' }}>
                {s.name}
              </button>
            ))}
          </div>
        </div>
        {(running || Object.keys(stages).length > 0) && (
          <ol className="grid gap-2 grid-cols-1 sm:grid-cols-5 mt-1">
            {STAGES.map((s, i) => {
              const st = stages[s.key]
              const color = st?.status === 'done' ? 'var(--good)' : st ? 'var(--s1)' : 'var(--axis)'
              return (
                <li key={s.key} className="rounded-lg p-2 text-xs" style={{ border: '1px solid var(--border)' }}>
                  <div className="flex items-center gap-1.5 font-medium" style={{ color: 'var(--ink)' }}>
                    <span className={`w-2 h-2 rounded-full ${st && st.status !== 'done' ? 'animate-pulse' : ''}`} style={{ background: color }} />
                    {i + 1}. {s.label}
                  </div>
                  <div className="mt-1 tabular" style={{ color: 'var(--ink-2)' }}>{st?.info || (st ? '…' : 'waiting')}</div>
                </li>
              )
            })}
          </ol>
        )}
        {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}
      </ChartCard>

      {batch && (
        <>
          <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
            <StatTile label="Batch" value={batch.batch_id} note={batch.filename} />
            <StatTile label="Transfers" value={fmtInt(batch.rows)} note={`${fmtInt(batch.accounts)} accounts · ${fmtUsd(batch.volume_usd)}`} />
            <StatTile label="Flagged transfers" value={fmtInt(batch.flagged_tx)}
              note={`${fmtPct(batch.flagged_tx / Math.max(batch.rows, 1))} of the batch`} />
            <StatTile label="Detector hits" value={fmtInt(batch.detector_hits)} note="Cypher typology detectors" />
            <StatTile label="Cases" value={fmtInt(batch.cases.length)} note={`${investigated.length} investigated`} />
            <StatTile label="Pipeline time" value={`${(Object.values(batch.timings_ms).reduce((a, b) => a + b, 0) / 1000).toFixed(1)}s`}
              note={Object.entries(batch.timings_ms).map(([k, v]) => `${k.replace('_ms', '')} ${v}ms`).join(' · ')} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <ChartCard title="Model score distribution" subtitle="Most transfers score near zero; cases come from the right tail and detector hits.">
              <div className="h-48">
                <ResponsiveContainer>
                  <BarChart data={hist} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <XAxis dataKey="bin" {...axisProps} interval={3} />
                    <YAxis {...axisProps} axisLine={false} scale="sqrt" width={40} />
                    {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                    <Tooltip cursor={{ fill: 'var(--grid)' }} content={({ active, payload }: any) => active && payload?.length
                      ? <TipBox title={`score ${payload[0].payload.bin}–${(Number(payload[0].payload.bin) + 0.05).toFixed(2)}`}
                        rows={[{ label: 'Transfers', value: fmtInt(payload[0].payload.n), color: 'var(--s1)' }]} /> : null} />
                    <Bar isAnimationActive={false} dataKey="n" fill="var(--s1)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </ChartCard>

            <ChartCard className="lg:col-span-2" title="Case queue"
              subtitle={batch.has_labels ? 'Ground truth column is for scoring only — the agent never sees it.' : undefined}>
              <div className="overflow-x-auto max-h-[420px]">
                <table className="w-full text-xs tabular">
                  <thead style={{ color: 'var(--muted)' }}>
                    <tr className="text-left">
                      <th className="py-1.5 pr-2 font-medium">Case</th>
                      <th className="py-1.5 pr-2 font-medium">Shape</th>
                      <th className="py-1.5 pr-2 font-medium text-right">Model</th>
                      <th className="py-1.5 pr-2 font-medium">Detectors</th>
                      <th className="py-1.5 pr-2 font-medium text-right">Amount</th>
                      <th className="py-1.5 pr-2 font-medium text-right">Accts</th>
                      <th className="py-1.5 pr-2 font-medium">Outcome</th>
                      {batch.has_labels && <th className="py-1.5 pr-2 font-medium">Truth</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {batch.cases.map((c) => (
                      <tr key={c.case_id} style={{ borderTop: '1px solid var(--border)' }}>
                        <td className="py-1.5 pr-2">
                          <Link to={`/case/${c.case_id}`} className="underline underline-offset-2" style={{ color: 'var(--ink)' }}>
                            {c.case_id.split('-').pop()}
                          </Link>
                        </td>
                        <td className="py-1.5 pr-2" style={{ color: 'var(--ink-2)' }}>{c.shape.typology}</td>
                        <td className="py-1.5 pr-2 text-right">{c.ml_score.toFixed(2)}</td>
                        <td className="py-1.5 pr-2" style={{ color: 'var(--ink-2)' }}>
                          {[...new Set(c.detector_hits.map((h) => h.detector))].join(', ') || '—'}
                        </td>
                        <td className="py-1.5 pr-2 text-right">{fmtUsd(c.amount)}</td>
                        <td className="py-1.5 pr-2 text-right">{c.accounts.length}</td>
                        <td className="py-1.5 pr-2">
                          {c.result ? <ActionBadge action={c.result.action} /> : (
                            <Link to={`/case/${c.case_id}?run=1`} className="text-xs px-2 py-0.5 rounded"
                              style={{ border: '1px solid var(--border)', color: 'var(--ink-2)' }}>Investigate</Link>
                          )}
                        </td>
                        {batch.has_labels && (
                          <td className="py-1.5 pr-2" style={{ color: 'var(--ink-2)' }}>
                            {c.ground_truth === 1 ? 'laundering' : c.ground_truth === 0 ? 'legit' : '—'}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </ChartCard>
          </div>
        </>
      )}
    </div>
  )
}
