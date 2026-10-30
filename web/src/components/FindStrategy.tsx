import { useState } from 'react'
import { api } from '../api/client'
import type { Research } from '../api/types'
import { Spinner } from './ui'

/** Ask about a strategy; Nemotron writes what the web says as rules the backtester can run, filling the
    gaps with house defaults it lists as "our choices". The rules only fill the prompt box: nothing runs
    until the trader has read them. */
export function FindStrategy({ instrument, timeframe, onUse }: {
  instrument: string
  timeframe: string
  onUse: (found: Research, checkedOn: { instrument: string; timeframe: string }) => void
}) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [found, setFound] = useState<Research | null>(null)
  const [foundOn, setFoundOn] = useState({ instrument, timeframe })  // what the rules were checked on
  const [error, setError] = useState<string | null>(null)

  async function search(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim() || busy) return
    setBusy(true)
    setError(null)
    setFound(null)
    try {
      setFound(await api.research(question.trim(), instrument, timeframe))
      setFoundOn({ instrument, timeframe })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card card-pad find">
      <form className="find-form" onSubmit={search}>
        <label className="eyebrow" htmlFor="find">Don't have one? Ask the web</label>
        <div className="find-row">
          <input id="find" className="input" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)}
            placeholder="What's the most famous ICT strategy?" />
          <button className="btn" disabled={!question.trim() || busy}>{busy ? <Spinner /> : null} Search</button>
        </div>
      </form>
      {busy && <p className="faint">Searching the web, writing the rules and checking they parse… about 10 seconds.</p>}
      {error && <div className="notice notice-neg">{error}</div>}
      {found && (
        <div className="found">
          <p>{found.summary}</p>
          {found.strategy && <blockquote className="found-rules">{found.strategy}</blockquote>}
          {found.parses === true && <span className="badge badge-pos found-check">✓ Checked: the backtester reads these rules without questions</span>}
          {found.parses === false && <span className="badge badge-warn found-check">The backtester may still ask a question or two</span>}
          {found.choices.length > 0 && (
            <div className="found-choices">
              <span className="found-list-head">Our choices <span className="faint">· the sources don't say, so we picked a standard default</span></span>
              <ul>{found.choices.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
          )}
          {found.left_out.length > 0 && (
            <div className="found-left-out">
              <span className="found-list-head">Left out</span>
              <ul>{found.left_out.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
          )}
          {found.sources.length > 0 && (
            <div className="found-sources faint">
              Sources:{' '}
              {found.sources.map((s, i) => (
                <span key={s.url}>{i > 0 && ' · '}<a href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a></span>
              ))}
            </div>
          )}
          {found.strategy && (
            <div className="found-use">
              <button className="btn btn-primary" onClick={() => onUse(found, foundOn)}>Use these rules</button>
              <span className="faint">Nemotron wrote these from the sources. Change any choice in the box before you test it.</span>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
