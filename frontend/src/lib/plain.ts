import type { ActionType, CaseBrief } from './api'

/** Plain-language names for everything the system shows, written for a junior analyst or a non-technical reader. */

export interface TrickInfo {
  name: string
  short: string
  why: string
  spot: string
}

export const TRICKS: Record<string, TrickInfo> = {
  'FAN-OUT': {
    name: 'Split & scatter',
    short: 'One account quickly sends money out to many different accounts.',
    why: 'Splitting a large sum into many smaller payments makes each one look ordinary and spreads the trail thin.',
    spot: 'Many receivers in a short time, most of them never paid before, often in similar amounts.',
  },
  'FAN-IN': {
    name: 'Collect',
    short: 'Many accounts send money into one account.',
    why: 'Money gathered from many places (for example, many small cash deposits) is pooled before it moves on.',
    spot: 'One account suddenly receives from many unrelated senders.',
  },
  'GATHER-SCATTER': {
    name: 'Collect, then scatter',
    short: 'Money is pooled into one account and then pushed out to many others.',
    why: 'The middle account works as a mixing point, so it is hard to tell which money went where.',
    spot: 'The same account both collects from many and pays out to many, within hours or days.',
  },
  'SCATTER-GATHER': {
    name: 'Scatter, then re-collect',
    short: 'Money is split across several middlemen and then reassembled in one place.',
    why: 'Routing through middlemen ("money mules") hides the direct link between where the money starts and where it ends.',
    spot: 'Several accounts each pass money from the same sender to the same final receiver.',
  },
  CYCLE: {
    name: 'Round trip',
    short: 'Money travels through a chain of accounts and comes back to where it started.',
    why: 'A round trip can make dirty money look like ordinary business income.',
    spot: 'Following payments forward in time leads back to the first account.',
  },
  BIPARTITE: {
    name: 'Group to group',
    short: 'A group of accounts pays another group of accounts directly.',
    why: 'Many-to-many payments blur which sender paid which receiver.',
    spot: 'Several senders and several receivers, with no middlemen, in a short window.',
  },
  STACK: {
    name: 'Layered relay',
    short: 'Money passes through layers of middlemen before reaching the end accounts.',
    why: 'Each extra layer is another hop an investigator has to trace.',
    spot: 'Money moves sender → middle layer → receivers, with each layer passing it on quickly.',
  },
  'PASS-THROUGH': {
    name: 'Pass-through',
    short: 'Money arrives and leaves almost straight away.',
    why: 'Accounts used only to pass money along ("mules") add distance between the criminal and the cash.',
    spot: 'Most of what comes in goes out again within hours.',
  },
  RANDOM: {
    name: 'Random hops',
    short: 'Money wanders through a chain of accounts with no clear shape.',
    why: 'An irregular path avoids the simple patterns that rules look for.',
    spot: 'A chain of quick hand-offs between accounts with no prior relationship.',
  },
}

export const trickName = (t?: string) => (t && TRICKS[t]?.name) || t || 'Unclassified'

/** Why a case is on the list, in plain words: its two strongest signals, e.g. "Unusual payments, Collect". */
export const caseReason = (signals: { signal: string; p: number }[]) =>
  signals.slice(0, 2).map((s) => (s.signal === 'model' ? 'Unusual payments' : trickName(s.signal))).join(', ')

/** Percent with one decimal below 10% so small detector precisions don't all read as "0%",
 *  and never "100%": a model score is not certainty. */
export const pct = (p: number) =>
  p >= 0.995 ? '>99%' : p < 0.1 ? `${(p * 100).toFixed(1)}%` : `${Math.round(p * 100)}%`

export const GLOSSARY: Record<string, string> = {
  alert: 'A payment or group of payments the system thinks deserves a closer look.',
  case: 'A group of connected suspicious payments investigated together.',
  risk: 'How strongly the evidence points to laundering, from 0 (none) to 1 (very strong).',
  escalate: 'Send to a senior analyst with a draft suspicious transaction report (STR). A human always decides.',
  review: 'The evidence is mixed. A human should look before deciding.',
  close: 'The activity is explained by normal behaviour (for example, regular salary or bill payments). Closed with reasons; reopened if anything new appears.',
  'red flag': 'A fact from the payment graph that points towards laundering.',
  'legitimate explanation': 'A fact from the payment graph that points towards normal activity.',
  STR: 'Suspicious Transaction Report, filed by the bank with its country’s Financial Intelligence Unit (FIU).',
  graph: 'A map of accounts (dots) and payments between them (arrows). FlowTrace stores it in FalkorDB, a graph database.',
}

export const SAMPLE_INFO: Record<string, { title: string; body: string }> = {
  'demo_mixed_2022-09-09.csv': {
    title: 'A normal day with hidden laundering',
    body: 'Real payments from 9 Sep 2022 in the dataset: 16 laundering schemes mixed in with busy honest businesses and everyday payments.',
  },
  'legit_lookalikes_2022-09-09.csv': {
    title: 'Busy but honest businesses',
    body: 'Real accounts that pay many people in one day. A simple rule would flag them; FlowTrace should explain why they look normal.',
  },
}

export const STEP_PLAIN: Record<string, { label: string; doing: string }> = {
  account_profile: { label: 'Who is this account?', doing: 'Checking the owner, today’s activity and how it compares with normal days' },
  counterparty_history: { label: 'Do they know these people?', doing: 'Checking whether the people paid (or paying) have dealt with this account before' },
  recurring_flows: { label: 'Is this a regular payment?', doing: 'Looking for salary, EMI, rent or bill patterns that repeat on a schedule' },
  trace_funds: { label: 'Follow the money', doing: 'Tracing where the money went next, hop by hop, in time order' },
  shared_counterparties: { label: 'Does it end up in one place?', doing: 'Checking whether several accounts send money to the same receiver' },
  find_cycles: { label: 'Does the money come back?', doing: 'Looking for loops that return money to the start' },
  owner_network: { label: 'Same owner?', doing: 'Checking whether the money only moves between one owner’s own accounts' },
  case_memory: { label: 'Seen before?', doing: 'Checking earlier cases and closeness to accounts already escalated' },
}

export const STAGE_PLAIN: Record<string, string> = {
  parse: 'Reading the file',
  score: 'Scoring every payment',
  graph: 'Adding payments to the money map',
  detect: 'Looking for suspicious shapes',
  cases: 'Grouping into cases',
}

/** A sentence cut to its first 12 words, without bracketed asides or evidence IDs ("(E1, E3)"). */
export function shortText(text: string, words = 12): string {
  const clean = text.replace(/\s*[([]\s*E\d+(?:\s*,\s*E\d+)*\s*[)\]]|\bE\d+\b/g, '').replace(/\s*\([^)]*\)/g, '').trim()
  const w = clean.split(/\s+/)
  return w.length <= words ? clean : `${w.slice(0, words).join(' ').replace(/[,;:]$/, '')}…`
}

/** "B1A2B3-C007" -> "7": the case's number in its upload. */
export function caseNo(id: string): string {
  const tail = id.slice(id.lastIndexOf('-') + 1)
  const n = Number(tail.replace(/^\D+/, ''))
  return Number.isFinite(n) && n > 0 ? String(n) : tail
}

/** An error a person can act on, instead of "…: 500 Internal Server Error". */
export function friendlyError(e: unknown): string {
  const s = e instanceof Error ? e.message : String(e)
  if (/: 5\d\d\b/.test(s) || /Failed to fetch|NetworkError/i.test(s)) return 'This could not be done right now (the graph database may be offline). Try again in a moment.'
  const m = s.match(/: \d{3} ([\s\S]*)$/)
  if (!m) return s
  try {
    const j = JSON.parse(m[1])
    if (j && typeof j.detail === 'string') return j.detail
  } catch { /* not JSON */ }
  return m[1]
}

export const shortAcct = (id: string) => {
  const acct = id.split(':')[1] ?? id
  return `…${acct.slice(-4)}`
}

/** What each action does, and the words on its approve button. */
export const ACTION_PLAIN: Record<ActionType, { title: string; approve: string }> = {
  HOLD: { title: 'Hold', approve: 'Approve hold' },
  RELEASE: { title: 'Release the hold', approve: 'Approve release' },
  ESCALATE: { title: 'Send to a senior analyst', approve: 'Approve escalation' },
  FILE_STR: { title: 'File an STR', approve: 'Approve filing' },
  CLOSE: { title: 'Close as normal activity', approve: 'Approve closing' },
}

/** One line on what the money did, in the data's own currency: "€33,855 passed through 3 accounts". */
export function caseSentence(typology: string | null, amount: string, accounts: number): string {
  switch (typology) {
    case 'FAN-OUT': return `${amount} split out to ${accounts - 1} accounts`
    case 'FAN-IN': return `${amount} collected from ${accounts - 1} accounts`
    case 'CYCLE': return `${amount} went round a loop of ${accounts} accounts`
    case 'GATHER-SCATTER': return `${amount} pooled, then pushed out again`
    case 'SCATTER-GATHER': return `${amount} split across middlemen, re-collected`
    case 'STACK': return `${amount} passed through layers of accounts`
    case 'BIPARTITE': return `${amount} paid group-to-group`
    default: return `${amount} moved between ${accounts} accounts`
  }
}

/** Today's work = within the team's daily capacity. Batches saved before tiers existed fall back to rank. */
export const inToday = (c: CaseBrief, i: number, capacity?: number) => (c.tier ? c.tier === 'today' : i < (capacity ?? 15))
