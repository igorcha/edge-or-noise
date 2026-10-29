const MINUS = '−'

/** +0.05R / −0.12R */
export function fmtR(x: number | null | undefined, digits = 2): string {
  if (x == null || Number.isNaN(x)) return '—'
  const s = Math.abs(x).toFixed(digits)
  return `${x < 0 && Number(s) !== 0 ? MINUS : '+'}${s}R`
}

export function fmtSigned(x: number | null | undefined, digits = 2): string {
  if (x == null || Number.isNaN(x)) return '—'
  const s = Math.abs(x).toFixed(digits)
  return `${x < 0 && Number(s) !== 0 ? MINUS : '+'}${s}`
}

export function fmtPct(x: number | null | undefined, digits = 0): string {
  if (x == null || Number.isNaN(x)) return '—'
  return `${(x * 100).toFixed(digits)}%`
}

export function fmtNum(x: number | null | undefined, digits = 0): string {
  if (x == null || Number.isNaN(x)) return '—'
  return x.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' })
}

export function plural(n: number, word: string, many = `${word}s`): string {
  return `${fmtNum(n)} ${n === 1 ? word : many}`
}

/** "max_trades_per_day" -> "Max trades per day" */
export function humanize(key: string): string {
  const s = key.replace(/_/g, ' ').replace(/\batr\b/gi, 'ATR').replace(/\bpts\b/, 'points')
  return s.charAt(0).toUpperCase() + s.slice(1)
}
