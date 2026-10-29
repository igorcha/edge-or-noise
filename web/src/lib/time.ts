// Traders think in New York time; the API speaks UTC.

const DAY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' })
const FULL = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
})
const CLOCK = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false })

/** Epoch seconds -> "Mar 3, 2021, 09:45" (full) or "09:45" / "Mar 3" at midnight (axis ticks). */
export function nyTime(epochSeconds: number, full = false): string {
  const d = new Date(epochSeconds * 1000)
  if (full) return FULL.format(d)
  const clock = CLOCK.format(d)
  return clock === '00:00' ? DAY.format(d) : clock
}

export function nyDate(iso: string): string {
  return FULL.format(new Date(iso))
}

const WEEKDAY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short' })
const DATE = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: 'short', day: 'numeric' })

/** The holdout's first trading day, as "Jun 16, 2025". The boundary is a 17:00 NY close, so the holdout
    starts with the next trading day: the one after on weekdays, Monday after a Friday. */
export function holdoutFirstDay(boundaryIso: string | null): string {
  if (!boundaryIso) return '—'
  const d = new Date(boundaryIso)
  const wd = WEEKDAY.format(d)
  const days = wd === 'Fri' ? 3 : wd === 'Sat' ? 2 : 1
  return DATE.format(new Date(d.getTime() + days * 86_400_000))
}
