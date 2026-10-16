import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON, postJSON, streamJob, type CaseResult, type JobBrief, type JobEvent } from './api'

export interface LiveStep { step: number; tool: string; reason: string; done: boolean; found: string[] }

/** A case's investigation: its stored result, and the agent's steps live while a job runs. Events are replayed
 *  from the start, so joining a running job late loses nothing. Events from a stream that belongs to a case the
 *  analyst has left are dropped. */
export function useInvestigation(caseId: string | null, jobId: string | null) {
  const [steps, setSteps] = useState<LiveStep[]>([])
  const [result, setResult] = useState<CaseResult | null>(null)
  const [live, setLive] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ended, setEnded] = useState(0)  // bumps whenever a stream ends, so the page reloads its data
  const current = useRef(caseId)
  const following = useRef<string | null>(null)
  const stop = useRef<(() => void) | null>(null)

  const follow = useCallback((id: string, cid: string) => {
    if (following.current === id || current.current !== cid) return
    stop.current?.()
    following.current = id
    setLive(true); setSteps([]); setError(null)
    stop.current = streamJob(id, (e: JobEvent) => {
      if (current.current !== cid) return
      if (e.type === 'step_start') setSteps((s) => [...s, { step: e.step, tool: e.tool, reason: e.reason, done: false, found: [] }])
      if (e.type === 'step_done') setSteps((s) => s.map((x) => x.step === e.step
        ? { ...x, done: true, found: (e.evidence ?? []).filter((v: { kind: string }) => v.kind !== 'context').map((v: { claim: string }) => v.claim) } : x))
      if (e.type === 'error') setError(e.error)
      if (e.type === 'cancelled') setError(e.reason === 'shutdown'
        ? 'The server stopped, so this run was stopped. It is investigated again when the server restarts.'
        : 'This file was uploaded again, so this run was stopped.')
    }, () => {
      if (current.current !== cid) return
      setLive(false); stop.current = null; following.current = null
      // The stored result is the truth: a cancelled run's report is not this case's result.
      getJSON<{ result: CaseResult | null }>(`/api/cases/${cid}`).then((r) => { if (current.current === cid) setResult(r.result) }).catch(() => undefined)
      setEnded((n) => n + 1)
    })
  }, [])

  useEffect(() => {
    current.current = caseId
    setSteps([]); setResult(null); setLive(false); setError(null)
    if (!caseId) return
    let stale = false
    getJSON<{ result: CaseResult | null; job: JobBrief | null }>(`/api/cases/${caseId}`).then((r) => {
      if (stale) return
      setResult(r.result)
      if (r.job) follow(r.job.id, caseId)
    }).catch(() => undefined)
    return () => { stale = true; stop.current?.(); stop.current = null; following.current = null }
  }, [caseId, follow])

  // A job that appears after the case was opened (queued in the background, or started elsewhere).
  useEffect(() => { if (jobId && caseId) follow(jobId, caseId) }, [jobId, caseId, follow])

  const investigate = useCallback(async () => {
    const cid = caseId
    if (!cid) return
    setError(null)
    try {
      const { job_id } = await postJSON<{ job_id: string }>(`/api/cases/${cid}/investigate?policy=llm`)
      follow(job_id, cid)
    } catch (err) { setError(String(err)) }
  }, [caseId, follow])

  return { steps, result, live, error, ended, investigate }
}
