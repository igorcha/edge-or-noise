import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import { useCatalog } from '../lib/catalog'
import { EXAMPLES } from '../lib/examples'
import type { ParseResult, Project, Spec } from '../api/types'
import { ParamEditor } from './ParamEditor'
import type { Draft } from './Workspace'
import { Spinner } from './ui'

const SECTIONS: [string, string][] = [
  ['direction', 'Market'], ['filters', 'Filters'], ['setup', 'Setup'], ['entry', 'Entry'],
  ['stop', 'Stop'], ['target', 'Target'], ['limits', 'Limits & costs'],
]

type Props = {
  project: Project
  draft: Draft
  onEdit: (spec: Spec | null) => void
  onRun: () => void
  onView: (trialNumber: number) => void
  running: boolean
  runError: string | null
  initialPrompt: string | null
  initialParsed: ParseResult | null  // rules found on the web, already parsed when they were checked
}

export function RulesPanel({ project, draft, onEdit, onRun, onView, running, runError, initialPrompt, initialParsed }: Props) {
  const locked = project.exam_used || project.is_demo
  return (
    <div className="rules-panel">
      <div className="rules-scroll">
        {!locked && <PromptBox project={project} onSpec={onEdit} initialPrompt={initialPrompt} initialParsed={initialParsed}
          hasRules={!!draft.spec} />}
        {draft.spec ? (
          <>
            <RulesCard draft={draft} />
            {!locked && <ParamEditor spec={draft.spec} onChange={onEdit} />}
          </>
        ) : !locked && <StartFromExample project={project} onPick={onEdit} />}
      </div>
      <RunBar project={project} draft={draft} running={running} runError={runError} onRun={onRun} onView={onView} />
    </div>
  )
}

function StartFromExample({ project, onPick }: { project: Project; onPick: (s: Spec) => void }) {
  const { examples } = useCatalog()
  const keys = Object.keys(EXAMPLES).filter((k) => examples[k])
  const same = keys.filter((k) => examples[k].timeframe === project.timeframe)
  return (
    <section className="rules-card">
      <h2 className="eyebrow">Or start from an example</h2>
      <div className="example-list">
        {(same.length ? same : keys).map((k) => (
          <button key={k} className="example-pick" onClick={() => onPick({ ...examples[k], timeframe: project.timeframe })}>
            <span className="example-title">{EXAMPLES[k].title}</span>
            <span className="muted">{EXAMPLES[k].text}</span>
          </button>
        ))}
      </div>
    </section>
  )
}

function RulesCard({ draft }: { draft: Draft }) {
  const sections = draft.preview?.card.sections
  return (
    <section className="rules-card">
      <div className="section-head">
        <h2 className="eyebrow">Rules being tested</h2>
        {draft.checking && <Spinner />}
      </div>
      {sections ? (
        <dl className="rules-list">
          {SECTIONS.map(([key, label]) => sections[key] && (
            <div key={key} className="rules-row">
              <dt>{label}</dt>
              <dd>{key === 'setup' ? steps(sections[key]).map((x) => <span key={x} className="rules-step">{x}</span>) : sections[key]}</dd>
            </div>
          ))}
        </dl>
      ) : !draft.error && <p className="faint">Checking the rules…</p>}
    </section>
  )
}

/** "1. A happens 2. B happens, within 10 bars" -> one line per step. */
function steps(text: string): string[] {
  return text.split(/\s(?=\d+\.\s)/)
}

function RunBar({ project, draft, running, runError, onRun, onView }: {
  project: Project; draft: Draft; running: boolean; runError: string | null; onRun: () => void; onView: (n: number) => void
}) {
  if (project.is_demo) {
    return <footer className="run-bar"><p className="faint">This is the shared demo. Start your own project to run trials.</p></footer>
  }
  if (project.exam_used) {
    return <footer className="run-bar"><p className="faint">The final exam is taken, so these rules are locked. Start a new project to keep exploring, knowing its holdout isn't unseen any more.</p></footer>
  }
  const p = draft.preview
  const blocked = !draft.spec || draft.checking || !!draft.error || !p
  return (
    <footer className="run-bar">
      {draft.error && (
        <div className="notice notice-neg">
          {draft.error.path ? <><code>{draft.error.path}</code>: </> : null}{draft.error.message}
        </div>
      )}
      {runError && <div className="notice notice-neg">{runError}</div>}
      {p?.trial.existing ? (
        <>
          <button className="btn btn-lg btn-block" onClick={() => onView(p.trial.existing!)}>View trial {p.trial.existing}</button>
          <p className="run-note faint">These exact rules already ran as trial {p.trial.existing}. Running them again wouldn't count.</p>
        </>
      ) : (
        <>
          <button className="btn btn-primary btn-lg btn-block" disabled={blocked || running} onClick={onRun}>
            {running ? <><Spinner /> Running trial {p?.trial.next}…</> : `Run as trial ${p?.trial.next ?? project.trial_count + 1}`}
          </button>
          <p className="run-note faint">
            Every new set of rules counts as a trial, and each one makes the verdict stricter.
          </p>
        </>
      )}
    </footer>
  )
}

// --- describing the strategy -----------------------------------------------------------------------------

function PromptBox({ project, onSpec, initialPrompt, initialParsed, hasRules }: {
  project: Project; onSpec: (s: Spec) => void; initialPrompt: string | null; initialParsed: ParseResult | null; hasRules: boolean
}) {
  const [prompt, setPrompt] = useState(initialPrompt ?? '')
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [result, setResult] = useState<ParseResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(!hasRules || !!initialPrompt)
  const autoRan = useRef(false)

  async function parse(withAnswers: boolean) {
    setBusy(true)
    setError(null)
    try {
      const qa = withAnswers && result ? result.questions.map((q) => ({ question: q, answer: answers[q] ?? '' })).filter((a) => a.answer.trim()) : []
      const r = await api.parse(project.id, prompt, qa)
      setResult(r)
      setAnswers({})
      if (r.status === 'ready' && r.spec) onSpec(r.spec)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {  // a project started from the home page's prompt parses it right away
    if (initialPrompt && !autoRan.current) {
      autoRan.current = true
      if (initialParsed?.status === 'ready' && initialParsed.spec) {
        // found on the web and parsed when it was checked: start from exactly that, not a second, different read
        setResult(initialParsed)
        onSpec(initialParsed.spec)
      } else {
        parse(false)
      }
    }
  }, [])  // oxlint-disable-line react-hooks/exhaustive-deps -- once, for the prompt the project started with

  if (!open) {
    return <button className="btn btn-ghost prompt-reopen" onClick={() => setOpen(true)}>Describe it differently</button>
  }
  return (
    <section className="prompt-box">
      <div className="section-head">
        <h2 className="eyebrow">Describe the strategy</h2>
        {result?.status === 'ready' && <button className="btn btn-ghost" onClick={() => setOpen(false)}>Hide</button>}
      </div>
      <textarea className="textarea" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
        placeholder="After a sweep of the Asia low in the NY AM killzone, buy the first bullish FVG at its top. Stop under the swing low, 2R." />
      <button className="btn" disabled={!prompt.trim() || busy} onClick={() => parse(false)}>
        {busy ? <><Spinner /> Reading…</> : 'Write the rules'}
      </button>
      {error && <div className="notice notice-neg">{error}</div>}
      {result && <ParseFeedback result={result} answers={answers} setAnswers={setAnswers} onContinue={() => parse(true)} busy={busy} />}
    </section>
  )
}

function ParseFeedback({ result, answers, setAnswers, onContinue, busy }: {
  result: ParseResult; answers: Record<string, string>; setAnswers: (a: Record<string, string>) => void; onContinue: () => void; busy: boolean
}) {
  return (
    <div className="parse-feedback">
      {result.status === 'questions' && (
        <div className="questions">
          <p className="field-label">A few things to pin down first:</p>
          {result.questions.map((q) => (
            <label key={q} className="question">
              <span>{q}</span>
              <input className="input" value={answers[q] ?? ''} onChange={(e) => setAnswers({ ...answers, [q]: e.target.value })} />
            </label>
          ))}
          <button className="btn btn-primary" disabled={busy || !Object.values(answers).some((a) => a.trim())} onClick={onContinue}>Continue</button>
        </div>
      )}
      {result.status === 'invalid' && <div className="notice notice-neg">Couldn't turn that into valid rules: {result.error}</div>}
      {result.unsupported.length > 0 && (
        <div className="notice notice-warn">
          <strong>Not tested:</strong>{' '}
          {result.unsupported.map((u) => `${u.concept} (${u.reason})`).join('; ')}
        </div>
      )}
      {result.assumptions.length > 0 && <Notes title="Read as" items={result.assumptions} />}
      {result.defaults.length > 0 && <Notes title="Defaults used" items={result.defaults} />}
    </div>
  )
}

function Notes({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="notes">
      <span className="field-label">{title}</span>
      <ul>{items.map((x) => <li key={x}>{x}</li>)}</ul>
    </div>
  )
}
