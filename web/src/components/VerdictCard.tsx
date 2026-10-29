import type { Trial } from '../api/types'
import { fmtNum, fmtPct, fmtR, plural } from '../lib/format'
import { LABELS, explain } from '../lib/verdict'

/** The verdict, and the four numbers it rests on. The first thing anyone looks at, so it says
    plainly how sure it is, and never more than that. */
export function VerdictCard({ trial }: { trial: Trial }) {
  const v = trial.verdict
  const { title, tone } = LABELS[v.label]
  const holdout = v.stage === 'holdout'
  const stress = trial.stress
  return (
    <section className={`card verdict verdict-${tone}`}>
      <div className="verdict-head">
        <span className="eyebrow">{holdout ? 'Holdout verdict · data it never saw' : 'Development verdict'}</span>
        <h3 className={`verdict-label ${tone}`}>{title}</h3>
        <p className="verdict-why">{explain(v)}</p>
      </div>

      <div className="verdict-stats">
        <div className="stat">
          <span className="stat-label">Average per trade</span>
          <span className={`stat-value num ${(v.expectancy_R ?? 0) > 0 ? 'pos' : (v.expectancy_R ?? 0) < 0 ? 'neg' : ''}`}>{fmtR(v.expectancy_R)}</span>
          {v.expectancy_ci && <IntervalBar mean={v.expectancy_R ?? 0} lo={v.expectancy_ci[0]} hi={v.expectancy_ci[1]} />}
          <span className="stat-note">
            {v.expectancy_ci ? <>95% range {fmtR(v.expectancy_ci[0])} to {fmtR(v.expectancy_ci[1])}</> : 'not enough trades for a range'}
          </span>
        </div>

        <div className="stat">
          <span className="stat-label">Beats random entries</span>
          <span className="stat-value num">{fmtPct(v.baseline_beaten)}</span>
          {v.baseline_beaten != null && <ShareBar share={v.baseline_beaten} />}
          <span className="stat-note">
            of {fmtNum(trial.baseline.n_runs)} runs with the same exits and costs
          </span>
        </div>

        <div className="stat">
          <span className="stat-label">{holdout ? 'Chance it\'s real' : 'Chance it beats luck'}</span>
          <span className="stat-value num">{v.deflated_sharpe != null ? v.deflated_sharpe.toFixed(2) : '—'}</span>
          {v.deflated_sharpe != null && <ShareBar share={v.deflated_sharpe} marks={[0.5, 0.95]} />}
          <span className="stat-note">
            {holdout ? 'one test on unseen data, not deflated' : <>deflated Sharpe, after {plural(v.n_trials, 'trial')}</>}
          </span>
        </div>

        <div className="stat">
          <span className="stat-label">Trades</span>
          <span className="stat-value num">{fmtNum(v.n)}</span>
          <span className="stat-note">
            {v.trades_needed && v.trades_needed > 10000 ? <>over 10,000 more to settle it: any edge is tiny</>
              : v.trades_needed ? <>about {fmtNum(v.trades_needed)} more to settle it</> : v.expectancy_R != null && v.expectancy_R > 0 && v.expectancy_ci && v.expectancy_ci[0] > 0 ? 'enough to rule out zero' : 'more trades won\'t make it positive'}
          </span>
        </div>
      </div>

      {stress && stress.stressed_expectancy_R != null && (
        <div className="verdict-foot">
          <span className="faint">Costs ×{stress.factor}</span>
          <span className={`num ${stress.survives ? 'pos' : 'neg'}`}>{fmtR(stress.stressed_expectancy_R)}</span>
          <span className="faint">{stress.survives ? 'still positive with harsher spread, slippage and commission' : 'gone once costs are a bit worse'}</span>
        </div>
      )}
    </section>
  )
}

/** The 95% interval around zero. Whether the bar crosses the zero line is the whole story. */
function IntervalBar({ mean, lo, hi }: { mean: number; lo: number; hi: number }) {
  const span = Math.max(Math.abs(lo), Math.abs(hi), 0.05) * 1.15
  const x = (r: number) => 50 + (r / span) * 50
  const tone = lo > 0 ? 'pos' : hi < 0 ? 'neg' : 'warn'
  return (
    <svg className="interval" viewBox="0 0 100 14" preserveAspectRatio="none" role="img"
      aria-label={`95% interval ${lo.toFixed(2)} to ${hi.toFixed(2)} R`}>
      <line x1="0" x2="100" y1="7" y2="7" className="interval-axis" />
      <line x1="50" x2="50" y1="1" y2="13" className="interval-zero" />
      <rect x={x(lo)} y="4" width={Math.max(x(hi) - x(lo), 0.8)} height="6" rx="1" className={`interval-range ${tone}`} />
      <rect x={x(mean) - 0.6} y="2" width="1.2" height="10" className="interval-mean" />
    </svg>
  )
}

function ShareBar({ share, marks = [0.5, 0.95] }: { share: number; marks?: number[] }) {
  return (
    <div className="share-bar" role="img" aria-label={fmtPct(share)}>
      <div className="share-fill" style={{ width: `${Math.min(Math.max(share, 0), 1) * 100}%` }} />
      {marks.map((m) => <span key={m} className="share-mark" style={{ left: `${m * 100}%` }} />)}
    </div>
  )
}
