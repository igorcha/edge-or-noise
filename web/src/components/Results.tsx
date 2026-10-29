import { useState } from 'react'
import { ExamModal } from './ExamModal'
import type { Project, Spec, Trial, TrialSummary } from '../api/types'
import { fmtDate, fmtPct, fmtR, plural } from '../lib/format'
import { LABELS } from '../lib/verdict'
import { EquityChart } from './EquityChart'
import { MetricsRow } from './MetricsRow'
import { TradeChart } from './TradeChart'
import { TradeList } from './TradeList'
import { VerdictCard } from './VerdictCard'
import { LockIcon, Spinner } from './ui'

type Props = {
  project: Project
  trials: TrialSummary[]
  selected: Trial | null
  onSelect: (id: number) => void
  onEditFrom: (spec: Spec) => void
  onExam: (trialId: number) => Promise<void>
  running: boolean
}

export function Results({ project, trials, selected, onSelect, onEditFrom, onExam, running }: Props) {
  const [examFor, setExamFor] = useState<Trial | null>(null)
  if (trials.length === 0) {
    return (
      <div className="results-empty">
        {running ? <><Spinner /><p className="muted">Running the first trial and 1,000 random-entry runs…</p></> : (
          <>
            <h2>No trials yet</h2>
            <p className="muted">
              Write the rules on the left and run them. You'll get a verdict, the equity curve against random entries
              with the same exits, and every trade on the chart.
            </p>
          </>
        )}
      </div>
    )
  }
  return (
    <div className="results">
      <TrialStrip trials={trials} selectedId={selected?.id ?? null} onSelect={onSelect} />
      {selected && (
        <>
          <div className="trial-head">
            <div>
              <h2 className="trial-title">
                {selected.stage === 'holdout' ? <>Final exam of trial {selected.number}</> : <>Trial {selected.number}</>}
              </h2>
              <p className="faint">
                {selected.stage === 'holdout' ? 'Run once on the sealed holdout'
                  : selected.summary === 'first run' ? 'First run' : `Changed: ${selected.summary}`} · {fmtDate(selected.created_at)}
              </p>
            </div>
            {selected.stage === 'development' && !project.exam_used && !project.is_demo && (
              <div className="trial-actions">
                <button className="btn btn-ghost" onClick={() => onEditFrom(selected.spec)}>Edit from these rules</button>
                <button className="btn" onClick={() => setExamFor(selected)}><LockIcon /> Final exam</button>
              </div>
            )}
          </div>
          {selected.stage === 'holdout' && <ExamComparison exam={selected} trials={trials} />}
          <VerdictCard trial={selected} />
          <MetricsRow trial={selected} />
          {selected.trades.length > 0 && <Trades key={selected.id} trial={selected} />}
        </>
      )}
      {examFor && (
        <ExamModal project={project} trial={examFor} onClose={() => setExamFor(null)}
          onRun={async () => { await onExam(examFor.id); setExamFor(null) }} />
      )}
    </div>
  )
}

/** Development next to holdout, for the same rules: the comparison the whole app exists for. */
function ExamComparison({ exam, trials }: { exam: Trial; trials: TrialSummary[] }) {
  const dev = trials.find((t) => t.stage === 'development' && t.number === exam.number)
  if (!dev) return null
  const side = (label: string, t: { expectancy_R: number | null; n: number | null; verdict: string | null }) => (
    <div className="compare-side">
      <span className="stat-label">{label}</span>
      <span className={`compare-value num ${(t.expectancy_R ?? 0) > 0 ? 'pos' : 'neg'}`}>{fmtR(t.expectancy_R)}</span>
      <span className="stat-note">{plural(t.n ?? 0, 'trade')} · {t.verdict ?? '—'}</span>
    </div>
  )
  return (
    <section className="card compare">
      {side(`Development, trial ${dev.number}`, dev)}
      <span className="compare-arrow" aria-hidden="true">→</span>
      {side('Holdout, never seen', { expectancy_R: exam.verdict.expectancy_R, n: exam.verdict.n, verdict: exam.verdict.label })}
    </section>
  )
}

function TrialStrip({ trials, selectedId, onSelect }: { trials: TrialSummary[]; selectedId: number | null; onSelect: (id: number) => void }) {
  return (
    <nav className="trial-strip" aria-label="Trials">
      {trials.map((t) => {
        const tone = t.verdict ? LABELS[t.verdict].tone : undefined
        const exam = t.stage === 'holdout'
        return (
          <button key={t.id} className={`trial-chip${t.id === selectedId ? ' on' : ''}${exam ? ' exam' : ''}`}
            onClick={() => onSelect(t.id)}
            title={`${exam ? 'Final exam' : `Trial ${t.number}`}: ${t.verdict ?? ''} · ${plural(t.n ?? 0, 'trade')} · ${fmtR(t.expectancy_R)}${t.baseline_beaten != null ? ` · beat ${fmtPct(t.baseline_beaten)} of random` : ''}`}>
            {exam ? <LockIcon open /> : null}
            <span className="num">{exam ? 'Exam' : t.number}</span>
            {tone && <span className={`chip-dot ${tone}`} />}
          </button>
        )
      })}
    </nav>
  )
}

function Trades({ trial }: { trial: Trial }) {
  const [pick, setPick] = useState(0)
  return (
    <>
      <EquityChart trial={trial} selected={pick} onPick={setPick} />
      <TradeChart trial={trial} index={pick} />
      <TradeList trades={trial.trades} selected={pick} onSelect={setPick} />
    </>
  )
}
