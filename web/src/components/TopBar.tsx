import type { Project, TrialSummary } from '../api/types'
import { holdoutFirstDay } from '../lib/time'
import { Badge, LockIcon, Logo } from './ui'

export function TopBar({ project, trials }: { project: Project; trials: TrialSummary[] }) {
  const n = project.trial_count
  const sealedFrom = holdoutFirstDay(project.holdout_boundary)
  const examined = trials.some((t) => t.stage === 'holdout')
  return (
    <header className="topbar">
      <a className="topbar-brand" href="#/" title="All projects"><Logo /><span className="brand">Edge or Noise</span></a>
      <span className="topbar-sep" />
      <div className="topbar-project">
        <span className="topbar-name">{project.name}</span>
        <span className="faint">{project.instrument} · {project.timeframe}</span>
        {project.is_demo && <Badge tone="accent">Demo</Badge>}
      </div>
      <div className="topbar-right">
        <div className="trial-counter" title="Every new set of rules is a trial. The verdict is deflated for how many you've run.">
          <span className="trial-counter-n num">{n}</span>
          <span className="trial-counter-label">{n === 1 ? 'trial' : 'trials'} counted</span>
        </div>
        {project.exam_used || examined ? (
          <Badge tone="accent" title="The holdout has been used. The project's rules are locked."><LockIcon open /> Holdout used</Badge>
        ) : (
          <Badge title={`Data from ${sealedFrom} on is sealed until the final exam.`}><LockIcon /> Holdout sealed from {sealedFrom}</Badge>
        )}
      </div>
    </header>
  )
}
