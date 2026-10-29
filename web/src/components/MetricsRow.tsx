import type { Trial } from '../api/types'
import { fmtNum, fmtPct, fmtR } from '../lib/format'

const EXITS: [string, string][] = [['target', 'Target'], ['stop', 'Stop'], ['time', 'Time'], ['end', 'Open at end']]

export function MetricsRow({ trial }: { trial: Trial }) {
  const m = trial.metrics
  const total = Object.values(m.exit_mix).reduce((a, b) => a + b, 0)
  const filtered = trial.skipped.filters ?? 0  // setups that formed outside the filters: never signals
  const skipped = Object.entries(trial.skipped).filter(([k, n]) => n > 0 && k !== 'filters')
  return (
    <section className="metrics">
      <Metric label="Win rate" value={fmtPct(m.win_rate)} />
      <Metric label="Profit factor" value={m.profit_factor != null ? m.profit_factor.toFixed(2) : '—'} />
      <Metric label="Total" value={fmtR(m.total_R, 1)} tone={m.total_R > 0 ? 'pos' : m.total_R < 0 ? 'neg' : undefined} />
      <Metric label="Max drawdown" value={m.max_drawdown_R != null ? `${m.max_drawdown_R.toFixed(1)}R` : '—'} />
      {/* Limit/stop fills and sells at the bid pay the spread in the fill itself, so nothing is charged on top. */}
      {m.cost_share === 0
        ? <Metric label="Costs" value="In fills" note="the spread is in the fill prices" />
        : <Metric label="Costs" value={fmtPct(m.cost_share)} note="of gross P&L" />}
      <Metric label="Avg hold" value={m.avg_bars_held != null ? `${fmtNum(m.avg_bars_held, 1)} bars` : '—'} />
      <div className="metric metric-wide">
        <span className="stat-label">Exits</span>
        <div className="exit-bar" role="img" aria-label="exit mix">
          {EXITS.map(([k]) => m.exit_mix[k] ? <span key={k} className={`exit-${k}`} style={{ flexGrow: m.exit_mix[k] }} /> : null)}
        </div>
        <span className="stat-note">
          {EXITS.filter(([k]) => m.exit_mix[k]).map(([k, label]) => `${label} ${fmtPct(m.exit_mix[k] / total)}`).join(' · ')}
        </span>
      </div>
      {(skipped.length > 0 || filtered > 0) && (
        <p className="metrics-skipped faint">
          {filtered > 0 && <>{fmtNum(filtered)} setups formed outside the filters. </>}
          {skipped.length > 0 && <>Signals not taken: {skipped.map(([k, n]) => `${fmtNum(n)} ${SKIPPED[k] ?? k}`).join(', ')}.</>}
        </p>
      )}
    </section>
  )
}

const SKIPPED: Record<string, string> = {
  busy: 'while a trade was open', daily_cap: 'over the daily cap', expired: 'limit never filled',
  bad_geometry: 'stop or target on the wrong side at the fill', no_data: 'at the end of the data',
  'stop not beyond entry': 'with the stop not beyond the entry',
}

function Metric({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: 'pos' | 'neg' }) {
  return (
    <div className="metric">
      <span className="stat-label">{label}</span>
      <span className={`metric-value num${tone ? ` ${tone}` : ''}`}>{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  )
}
