import { createContext, useContext, type ReactNode } from 'react'
import { shortAcct } from './plain'

/** Readable per-case account names ("Account A"), set by the case page from the server's mapping. */
const Names = createContext<Record<string, string>>({})

export function NamesProvider({ names, children }: { names: Record<string, string>; children: ReactNode }) {
  return <Names.Provider value={names}>{children}</Names.Provider>
}

/** id -> "Account A"; outside a case page it falls back to the short id. */
export function useName(): (id: string) => string {
  const names = useContext(Names)
  return (id: string) => names[id] ?? `Account ${shortAcct(id)}`
}

/** "Bank 40404 · 80F739E30": the full identity, for tooltips. */
export const fullId = (id: string) => {
  const [bank, acct] = id.includes(':') ? id.split(':') : ['', id]
  return bank ? `Bank ${bank} · ${acct}` : acct
}
