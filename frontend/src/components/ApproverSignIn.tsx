import { useId, useState } from 'react'
import { KeyRound, Loader2 } from 'lucide-react'
import { signIn } from '../lib/approver'

/** Sign in as the demo approver: the server checks the token; until then the visitor can only view. */
export default function ApproverSignIn({ compact = false }: { compact?: boolean }) {
  const id = useId()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!value.trim()) return
    setBusy(true); setError(null)
    try { await signIn(value); setValue('') } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-1.5">
      {!compact && (
        <p className="text-xs" style={{ color: 'var(--ink-2)' }}>
          You are viewing only. Approving or rejecting needs the <b style={{ color: 'var(--ink)' }}>demo approver</b> token.
        </p>
      )}
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor={id}>Approver token</label>
        <input id={id} type="password" value={value} autoComplete="off"
          onChange={(e) => setValue(e.target.value)} placeholder="Approver token"
          className="rounded-md px-2 py-1.5 text-sm min-w-0 flex-1"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--ink)' }} />
        <button type="submit" disabled={busy || !value.trim()}
          className="px-3 py-1.5 rounded-md text-sm font-medium flex items-center gap-1.5 whitespace-nowrap disabled:opacity-50"
          style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} aria-hidden />} Sign in
        </button>
      </div>
      {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}
    </form>
  )
}
