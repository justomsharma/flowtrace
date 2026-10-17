import { useSyncExternalStore } from 'react'

/**
 * The demo approver's sign-in. Approve and reject need the server's approver token; a visitor who has not signed
 * in can view everything but decide nothing. The token is checked by the server (POST /api/auth/approver) and kept
 * for this browser tab only (sessionStorage). In local development (vite dev only, never a build)
 * VITE_APPROVER_TOKEN signs in automatically.
 */
const KEY = 'flowtrace.approverToken'
const listeners = new Set<() => void>()

function read(): string | null {
  try { return sessionStorage.getItem(KEY) } catch { return null }
}

let token: string | null = read() ?? (import.meta.env.DEV ? import.meta.env.VITE_APPROVER_TOKEN || null : null)

function set(v: string | null) {
  token = v
  try { if (v) sessionStorage.setItem(KEY, v); else sessionStorage.removeItem(KEY) } catch { /* private window */ }
  listeners.forEach((l) => l())
}

/** Check the token with the server, then keep it. Throws with the server's message when it is wrong. */
export async function signIn(candidate: string): Promise<void> {
  const r = await fetch('/api/auth/approver', { method: 'POST', headers: { 'X-Approver-Token': candidate.trim() } })
  if (!r.ok) {
    let msg = `${r.status}`
    try { msg = ((await r.json()) as { detail?: string }).detail ?? msg } catch { /* not JSON */ }
    throw new Error(r.status === 401 ? 'That approver token is not right.' : msg)
  }
  set(candidate.trim())
}

export function signOut() { set(null) }

export function approverToken(): string | null { return token }

export function useApprover(): string | null {
  return useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l) } }, () => token)
}
