import type { Verdict, VerdictLabel } from '../api/types'
import { fmtNum, plural } from './format'

export type Tone = 'pos' | 'neg' | 'warn'

export const LABELS: Record<VerdictLabel, { title: string; tone: Tone }> = {
  'probably noise': { title: 'Probably noise', tone: 'neg' },
  "can't tell yet": { title: "Can't tell yet", tone: 'warn' },
  promising: { title: 'Promising', tone: 'pos' },
}

/** One plain sentence on why the verdict is what it is, built from the same numbers the engine used. */
export function explain(v: Verdict): string {
  const holdout = v.stage === 'holdout'
  if (v.n < 2) return 'Too few trades to say anything yet.'
  if (v.label === 'promising') {
    return holdout
      ? 'It held up on data it never saw: above zero, ahead of random entries.'
      : v.n_trials > 1
        ? `It beats random entries and survives the penalty for ${plural(v.n_trials, 'trial')}. The sealed holdout is the real test.`
        : 'It beats random entries on the first try. The sealed holdout is the real test.'
  }
  if (v.label === 'probably noise') {
    if (holdout) {
      return (v.expectancy_R ?? 0) <= 0
        ? 'On data it never saw, it loses on average once costs are paid.'
        : 'On data it never saw, random entries with the same exits did as well: the development result was luck.'
    }
    if ((v.expectancy_R ?? 0) <= 0) return 'It loses on average once costs are paid.'
    if (v.baseline_beaten != null && v.baseline_beaten < 0.5) {
      return 'Random entries with the same exits did better in most runs: the entries add nothing.'
    }
    return `After ${plural(v.n_trials, 'trial')}, a result this good is what luck alone tends to produce.`
  }
  if (v.n < 30) return `Only ${plural(v.n, 'trade')}: far too few to tell an edge from luck.`
  if (v.trades_needed && v.trades_needed > 10000) return 'The result could still be luck, and any edge is too small to ever settle.'
  if (v.trades_needed) {
    return `The result could still be luck. About ${fmtNum(v.trades_needed)} more trades would settle it.`
  }
  return 'The result could still be luck.'
}
