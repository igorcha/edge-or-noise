import { useEffect, useRef, useState } from 'react'
import type { Project, Trial } from '../api/types'
import { plural } from '../lib/format'
import { holdoutFirstDay } from '../lib/time'
import { LockIcon, Spinner } from './ui'

/** The one-time final exam. It spends the holdout, so it says exactly what that means before it runs. */
export function ExamModal({ project, trial, onRun, onClose }: {
  project: Project; trial: Trial; onRun: () => Promise<void>; onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cancel = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancel.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  async function run() {
    setBusy(true)
    setError(null)
    try {
      await onRun()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  const others = project.trial_count - 1
  return (
    <div className="modal-backdrop" onClick={() => !busy && onClose()}>
      <div className="modal card" role="dialog" aria-modal="true" aria-labelledby="exam-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-icon"><LockIcon /></div>
        <h2 id="exam-title">Final exam for trial {trial.number}</h2>
        <p className="muted">
          Everything from {holdoutFirstDay(project.holdout_boundary)} on has been sealed: no trial has seen it. The exam runs
          trial {trial.number}'s exact rules on it, once.
        </p>
        <ul className="modal-list">
          <li>It's one shot. The project's rules lock afterwards, and the holdout is no longer unseen.</li>
          {others > 0 && <li>You ran {plural(project.trial_count, 'trial')}. Picking the best-looking one is exactly what this checks.</li>}
          <li>The holdout verdict is final. A good development result that fades here was most likely luck.</li>
        </ul>
        {error && <div className="notice notice-neg">{error}</div>}
        <div className="modal-actions">
          <button ref={cancel} className="btn" disabled={busy} onClick={onClose}>Not yet</button>
          <button className="btn btn-primary" disabled={busy} onClick={run}>
            {busy ? <><Spinner /> Running the exam…</> : `Run the exam on trial ${trial.number}`}
          </button>
        </div>
      </div>
    </div>
  )
}
