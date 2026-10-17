import type { AutoHoldPolicy } from './api'

/** The hold promise, stated exactly for this server. Without auto-hold: nothing is frozen until a person approves.
 *  With it, the one exception is spelled out: when, for how long, and who ends it. */
export function holdPromise(p?: AutoHoldPolicy | null): string {
  if (!p?.active) return 'Nothing is frozen until you approve.'
  const days = p.confirm_business_days ?? 1
  return `Nothing is frozen until you approve, with one exception: when an investigation you start escalates with a confidence `
    + `score of ${(p.min_confidence ?? 0).toFixed(2)} or more, the agent holds the case money provisionally for `
    + `${days} business day${days === 1 ? '' : 's'}. You confirm or reject it; unconfirmed, it is released.`
}

/** Holds placed by the auto-hold policy (still provisional, or confirmed/released after it). */
export const isAutoHold = (a: { status: string; held_by?: string }) => a.status === 'PROVISIONAL' || !!a.held_by
