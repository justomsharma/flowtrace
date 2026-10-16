/** Money in the data's own currency. Graph amounts are US dollars (converted at the dataset's fixed rates);
 *  `rate` is units of `currency` per US dollar, so the original amount is usd * rate. */

const CODES: Record<string, string> = {
  'US Dollar': 'USD', Euro: 'EUR', 'UK Pound': 'GBP', Yuan: 'CNY', Yen: 'JPY', Rupee: 'INR', Ruble: 'RUB',
  'Canadian Dollar': 'CAD', 'Australian Dollar': 'AUD', 'Swiss Franc': 'CHF', 'Mexican Peso': 'MXN',
  Shekel: 'ILS', 'Brazil Real': 'BRL', 'Saudi Riyal': 'SAR',
}

export function fmtMoney(usd: number, currency = 'US Dollar', rate = 1): string {
  const v = usd * (rate || 1)
  if (currency === 'Bitcoin') return `₿${v.toLocaleString('en-US', { maximumFractionDigits: 4 })}`
  const code = CODES[currency]
  if (!code) return `$${Math.round(usd).toLocaleString('en-US')}`
  const compact = Math.abs(v) >= 1e7
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: code, maximumFractionDigits: compact ? 1 : 0,
    notation: compact ? 'compact' : 'standard',
  }).format(v)
}

/** "a mixed-currency trail" note: the plan falls back to US dollars when the trail used several currencies. */
export const currencyNote = (currency: string) =>
  currency === 'US Dollar' ? 'Amounts in US dollars.' : `Amounts in ${currency}, the currency of this money trail.`
