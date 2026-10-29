import { useEffect, useState } from 'react'
import { useCatalog } from '../lib/catalog'
import { api } from '../api/client'
import type { Project } from '../api/types'
import { EXAMPLES } from '../lib/examples'
import { recentProjects, rememberProject, setStart, type Start } from '../lib/store'
import { Logo, Segmented, Spinner } from './ui'


const TIMEFRAMES = [
  { value: '5m', label: '5 min' },
  { value: '15m', label: '15 min' },
  { value: '1m', label: '1 min' },
]

export function Home() {
  const catalog = useCatalog()
  const [prompt, setPrompt] = useState('')
  const [timeframe, setTimeframe] = useState('5m')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const recent = recentProjects()
  const [demo, setDemo] = useState<Project | null>(null)

  useEffect(() => {
    api.demo().then(setDemo, () => setDemo(null))  // no demo seeded: just don't offer it
  }, [])

  async function start(name: string, tf: string, how: Start, key: string) {
    setBusy(key)
    setError(null)
    try {
      const p = await api.createProject({ name, instrument: 'US100', timeframe: tf })
      rememberProject({ id: p.id, name: p.name, timeframe: p.timeframe })
      setStart(p.id, how)
      window.location.hash = `#/p/${p.id}`
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  const name = prompt.trim().split(/[.\n]/)[0].slice(0, 60) || 'Untitled strategy'
  return (
    <div className="home">
      <header className="home-head">
        <Logo />
        <span className="brand">Edge or Noise</span>
      </header>

      <section className="home-hero">
        <h1>Is it an edge, or noise?</h1>
        <p className="muted">
          Describe a strategy in plain English. It runs on six years of Nasdaq 100 data, against random entries
          with the same exits, with every trial counted and the last 16 months sealed for one final exam.
        </p>
      </section>

      {demo && (
        <a className="card demo-link" href={`#/p/${demo.id}`}>
          <span>
            <span className="example-title">See a finished project</span>
            <span className="muted"> · the FVG strategy, {demo.trial_count} trials and its final exam. Read-only, no signup.</span>
          </span>
          <span className="demo-arrow" aria-hidden="true">→</span>
        </a>
      )}

      <section className="card card-pad home-start">
        <label className="eyebrow" htmlFor="prompt">Your strategy</label>
        <textarea id="prompt" className="textarea" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="In the NY AM killzone, after price sweeps the Asia low, buy the first bullish fair value gap at its top. Stop below the swing low, target 2R." />
        <div className="home-start-row">
          <div className="home-tf">
            <span className="field-label">US100 · timeframe</span>
            <Segmented value={timeframe} options={TIMEFRAMES} onChange={setTimeframe} />
          </div>
          <button className="btn btn-primary btn-lg" disabled={!prompt.trim() || busy !== null}
            onClick={() => start(name, timeframe, { prompt: prompt.trim() }, 'prompt')}>
            {busy === 'prompt' ? <Spinner /> : null} Write the rules
          </button>
        </div>
        {error && <div className="notice notice-neg">{error}</div>}
      </section>

      <section className="home-examples">
        <h2 className="eyebrow">Or start from an example</h2>
        <div className="example-grid">
          {Object.entries(EXAMPLES).filter(([k]) => k in catalog.examples).map(([key, ex]) => {
            const tf = catalog.examples[key].timeframe
            return (
              <button key={key} className="card example" disabled={busy !== null}
                onClick={() => start(ex.title, tf, { example: key }, key)}>
                <span className="example-title">{ex.title} <span className="faint">· {tf}</span></span>
                <span className="muted">{ex.text}</span>
                {busy === key && <Spinner />}
              </button>
            )
          })}
        </div>
      </section>

      {recent.length > 0 && (
        <section className="home-recent">
          <h2 className="eyebrow">Your projects</h2>
          <ul>
            {recent.map((r) => (
              <li key={r.id}><a href={`#/p/${r.id}`}>{r.name}</a> <span className="faint">· US100 {r.timeframe}</span></li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
