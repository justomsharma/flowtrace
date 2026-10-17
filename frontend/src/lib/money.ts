/** Every amount in the app is the US dollar equivalent, as stored in the graph (the dataset's payments in other
 *  currencies were converted at its fixed rates). Each payment row also shows the payment in its own currency.
 *  The currency and rate arguments are kept for older callers and ignored. */
export function fmtMoney(usd: number, _currency?: string | null, _rate?: number | null): string {
  void _currency
  void _rate
  const compact = Math.abs(usd) >= 1e7
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: compact ? 1 : 0,
    notation: compact ? 'compact' : 'standard',
  }).format(usd)
}

export const USD_EQ = 'USD equivalent'

export const currencyNote = (_currency?: string) => {
  void _currency
  return "Amounts are the USD equivalent, converted at the dataset's fixed rates."
}

const CODES: Record<string, string> = {
  'US Dollar': 'USD', Euro: 'EUR', 'UK Pound': 'GBP', Yuan: 'CNY', Yen: 'JPY', Rupee: 'INR', Ruble: 'RUB',
  'Canadian Dollar': 'CAD', 'Australian Dollar': 'AUD', 'Swiss Franc': 'CHF', 'Mexican Peso': 'MXN', Shekel: 'ILS',
  'Brazil Real': 'BRL', 'Saudi Riyal': 'SAR', Bitcoin: 'BTC',
}

/** A payment in its own currency: "EUR 1,050", "BTC 0.0412". Empty when the currency is unknown. */
export function fmtOriginal(amount: number | null | undefined, currency: string | null | undefined): string {
  if (amount == null || !currency) return ''
  const code = CODES[currency] ?? currency
  const digits = code === 'BTC' ? 4 : 0
  return `${code} ${amount.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}
