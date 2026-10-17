/** The newest upload, or null when nothing was uploaded. */
export function newestBatch(bs: { batch_id: string; uploaded_at?: number }[]): string | null {
  return [...bs].sort((a, b) => (b.uploaded_at ?? 0) - (a.uploaded_at ?? 0))[0]?.batch_id ?? null
}

/** Where the home page ("/") sends a visitor: the newest upload's Today's work. It stays on the upload screen only
 *  when the URL picks a batch itself, asks for a fresh screen (?fresh=1), or nothing was uploaded yet. */
export function homeTarget(search: string, newest: string | null): string | null {
  const p = new URLSearchParams(search)
  if (p.has('batch') || p.get('fresh') === '1' || !newest) return null
  const q = new URLSearchParams({ batch: newest })
  if (p.get('case')) q.set('case', p.get('case')!)
  return `/today?${q}`
}
