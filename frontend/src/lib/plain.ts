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
  RANDOM: {
    name: 'Random hops',
    short: 'Money wanders through a chain of accounts with no clear shape.',
    why: 'An irregular path avoids the simple patterns that rules look for.',
    spot: 'A chain of quick hand-offs between accounts with no prior relationship.',
  },
}

export const trickName = (t?: string) => (t && TRICKS[t]?.name) || t || 'Unclassified'

export const GLOSSARY: Record<string, string> = {
  alert: 'A payment or group of payments the system thinks deserves a closer look.',
  case: 'A group of connected suspicious payments investigated together.',
  risk: 'How strongly the evidence points to laundering, from 0 (none) to 1 (very strong).',
  escalate: 'Send to a senior analyst with a draft suspicious transaction report (STR). A human always decides.',
  review: 'The evidence is mixed. A human should look before deciding.',
  close: 'The activity is explained by normal behaviour (for example, regular salary or bill payments). Closed with reasons; reopened if anything new appears.',
  'red flag': 'A fact from the payment graph that points towards laundering.',
  'legitimate explanation': 'A fact from the payment graph that points towards normal activity.',
  STR: 'Suspicious Transaction Report, filed by the bank with FIU-IND (India’s Financial Intelligence Unit).',
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

export const shortAcct = (id: string) => {
  const acct = id.split(':')[1] ?? id
  return `…${acct.slice(-4)}`
}
