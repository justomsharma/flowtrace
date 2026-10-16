import {
  AlertTriangle, ArrowRightLeft, Banknote, BriefcaseBusiness, CalendarClock, Clock, GitMerge, Handshake, History,
  Landmark, Moon, Repeat, RefreshCcw, Split, Store, TrendingUp, UserPlus, Users, Wallet, Zap, type LucideIcon,
} from 'lucide-react'

/** A short title for each kind of evidence the investigation tools produce (agent/tools.py). */
export const EVIDENCE: Record<string, { title: string; Icon: LucideIcon }> = {
  // red flags
  PASS_THROUGH: { title: 'Money passed straight through', Icon: ArrowRightLeft },
  FAST_ONWARD_TRANSFER: { title: 'Fast onward transfers', Icon: Zap },
  NEW_COUNTERPARTIES: { title: 'New counterparties', Icon: UserPlus },
  ROUND_TRIP: { title: 'Money came back', Icon: RefreshCcw },
  CYCLE: { title: 'Circular movement', Icon: RefreshCcw },
  EQUAL_SPLITS: { title: 'Equal-sized splits', Icon: Split },
  CASH_OR_CRYPTO_EXIT: { title: 'Left in cash or crypto', Icon: Banknote },
  HIGH_RISK_RAIL: { title: 'Hard-to-trace payment type', Icon: Banknote },
  ACTIVITY_SPIKE: { title: 'Unusual activity spike', Icon: TrendingUp },
  DORMANT_THEN_ACTIVE: { title: 'Dormant account woke up', Icon: Moon },
  CONVERGENCE: { title: 'Money converges on one account', Icon: GitMerge },
  MANY_SOURCES: { title: 'Many senders', Icon: Users },
  NEAR_ESCALATED: { title: 'Close to an escalated account', Icon: History },
  PRIOR_ESCALATION: { title: 'Escalated before', Icon: History },
  // legitimate explanations
  ESTABLISHED_COUNTERPARTIES: { title: 'Known counterparties', Icon: Handshake },
  RECURRING_FLOWS: { title: 'Regular, scheduled payments', Icon: CalendarClock },
  SALARY_DISTRIBUTION: { title: 'Salary paid out to bills', Icon: BriefcaseBusiness },
  CONSISTENT_WITH_BASELINE: { title: 'Normal for this account', Icon: Clock },
  PAYS_HIGH_VOLUME_RECEIVERS: { title: 'Pays merchants or lenders', Icon: Store },
  INTERNAL_TRANSFER: { title: "Between the owner's own accounts", Icon: Landmark },
  FUNDS_STOP: { title: 'The money stops moving', Icon: Wallet },
  BEHAVIOUR_MEMORY: { title: 'Seen before and explained', Icon: Repeat },
  PRIOR_CLOSURE: { title: 'Closed as normal before', Icon: Repeat },
}

export const evidenceInfo = (code: string) =>
  EVIDENCE[code] ?? { title: code.toLowerCase().replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), Icon: AlertTriangle }
