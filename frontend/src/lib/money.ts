/** Every amount in the app is shown in US dollars, as stored in the graph (the dataset's payments were
 *  converted at its fixed rates). The currency and rate arguments are kept for older callers and ignored. */
export function fmtMoney(usd: number, _currency?: string | null, _rate?: number | null): string {
  void _currency
  void _rate
  const compact = Math.abs(usd) >= 1e7
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: compact ? 1 : 0,
    notation: compact ? 'compact' : 'standard',
  }).format(usd)
}

export const currencyNote = (_currency?: string) => {
  void _currency
  return 'Amounts in US dollars.'
}
