import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getJSON, type BatchSummary } from './api'

interface BatchCtx {
  /** The batch in the URL, else the newest upload. */
  batchId: string | null
  caseId: string | null
  batch: BatchSummary | null
  /** False until the batch list (or the batch) has been asked for, so pages don't flash an empty state. */
  ready: boolean
  refresh: () => void
  /** "?batch=..&case=.." for a link that keeps the current selection. */
  query: (caseId?: string | null) => string
}

const Ctx = createContext<BatchCtx | null>(null)

export function BatchProvider({ children }: { children: ReactNode }) {
  const [params] = useSearchParams()
  const urlBatch = params.get('batch')
  const caseId = params.get('case')
  const [newest, setNewest] = useState<string | null>(null)
  const [listed, setListed] = useState(false)
  const [batch, setBatch] = useState<BatchSummary | null>(null)
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const batchId = urlBatch ?? newest

  useEffect(() => {
    if (urlBatch) return
    getJSON<{ batch_id: string; uploaded_at?: number }[]>('/api/batches')
      .then((bs) => setNewest([...bs].sort((a, b) => (b.uploaded_at ?? 0) - (a.uploaded_at ?? 0))[0]?.batch_id ?? null))
      .catch(() => setNewest(null))
      .finally(() => setListed(true))
  }, [urlBatch])

  const current = useRef(batchId)
  current.current = batchId
  const refresh = useCallback(() => {
    if (!batchId) return
    const id = batchId
    getJSON<BatchSummary>(`/api/batches/${id}`)
      .then((b) => { if (current.current === id) setBatch(b) })  // a reply for a batch no longer shown is dropped
      // Only a missing batch clears the queue; a failed poll (server restarting, network blip) keeps the last one.
      .catch((e) => { if (current.current === id && String(e).includes(': 404')) setBatch(null) })
      .finally(() => { if (current.current === id) setLoadedId(id) })
  }, [batchId])

  useEffect(() => { setBatch(null); refresh() }, [refresh])

  // While the top cases are investigated in the background, keep the queue fresh.
  const pf = batch?.prefetch
  const working = !!batch && ((!!pf && pf.done + pf.failed < pf.total) || batch.cases.some((c) => c.job))
  useEffect(() => {
    if (!working) return
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [working, refresh])

  const query = useCallback((c?: string | null) => {
    const q = new URLSearchParams()
    if (batchId) q.set('batch', batchId)
    const cid = c === undefined ? caseId : c
    if (cid) q.set('case', cid)
    const s = q.toString()
    return s ? `?${s}` : ''
  }, [batchId, caseId])

  const ready = urlBatch ? loadedId === urlBatch : listed && (!newest || loadedId === newest)
  const value = useMemo(() => ({ batchId, caseId, batch, ready, refresh, query }), [batchId, caseId, batch, ready, refresh, query])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useBatch(): BatchCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useBatch outside BatchProvider')
  return v
}
