import { useEffect, useState } from 'react'
import { useCatalog } from '../lib/catalog'
import { api } from '../api/client'
import type { ParseResult, Project } from '../api/types'
import { EXAMPLES } from '../lib/examples'
import { recentProjects, rememberProject, setStart, type Start } from '../lib/store'
import { FindStrategy } from './FindStrategy'
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
  const markets = catalog.markets.length ? catalog.markets : [{ name: 'US100', title: 'Nasdaq 100' }]
  const [instrument, setInstrument] = useState(markets[0].name)
  const titles = markets.map((m) => m.title)
  const marketList = titles.length > 1 ? `${titles.slice(0, -1).join(', ')} and ${titles.at(-1)}` : titles[0]
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const recent = recentProjects()
  const [demo, setDemo] = useState<Project | null>(null)
  // rules found on the web, with the parse made when they were checked on that market and timeframe
  const [found, setFound] = useState<{
    name: string; rules: string; parsed: ParseResult | null; instrument: string; timeframe: string
  } | null>(null)

  useEffect(() => {
    api.demo().then(setDemo, () => setDemo(null))  // no demo seeded: just don't offer it
  }, [])

  async function start(name: string, tf: string, how: Start, key: string) {
    setBusy(key)
    setError(null)
    try {
      const p = await api.createProject({ name, instrument, timeframe: tf })
      rememberProject({ id: p.id, name: p.name, instrument: p.instrument, timeframe: p.timeframe })
      setStart(p.id, how)
      window.location.hash = `#/p/${p.id}`
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  // the checked parse holds only for the exact rules on the market and timeframe it was made for: an edit parses again
  const checkedParse = () =>
    found && found.parsed && found.rules === prompt.trim() && found.instrument === instrument && found.timeframe === timeframe
      ? found.parsed : undefined
  const name = (found && found.rules === prompt.trim() && found.name)
    || prompt.trim().split(/[.\n]/)[0].slice(0, 60) || 'Untitled strategy'
  return (
    <div className="home">
      <header className="home-head">
        <Logo />
        <span className="brand">Edge or Noise</span>
      </header>

      <section className="home-hero">
        <h1>Is it an <span className="hl-edge">edge</span>, or <span className="hl-noise">noise</span>?</h1>
        <p className="muted">
          Describe a strategy in plain English. It runs on six-plus years of 1-minute {marketList} data, against
          random entries with the same exits, with every trial counted and the last 16 months sealed for one final exam.
        </p>
      </section>

      {demo && (
        <a className="card demo-link" href={`#/p/${demo.id}`}>
          <span>
            <span className="example-title">See a finished project</span>
            <span className="muted">
              {demo.exam_used
                ? ` · the FVG strategy: its best of ${demo.trial_count} trials looked positive. See what the sealed holdout said.`
                : ` · the FVG strategy, ${demo.trial_count} trials.`}{' '}
              Read-only, no signup.
            </span>
          </span>
          <span className="demo-arrow" aria-hidden="true">→</span>
        </a>
      )}

      <section className="card card-pad home-start">
        <label className="eyebrow" htmlFor="prompt">Your strategy</label>
        <textarea id="prompt" className="textarea" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="In the NY AM killzone, after price sweeps the Asia low, buy the first bullish fair value gap at its top. Stop below the swing low, target 2R." />
        <div className="home-start-row">
          <div className="home-pickers">
            {markets.length > 1 && (
              <div className="home-tf">
                <span className="field-label">Market</span>
                <Segmented value={instrument} options={markets.map((m) => ({ value: m.name, label: m.title }))} onChange={setInstrument} />
              </div>
            )}
            <div className="home-tf">
              <span className="field-label">{markets.length > 1 ? 'Timeframe' : `${instrument} · timeframe`}</span>
              <Segmented value={timeframe} options={TIMEFRAMES} onChange={setTimeframe} />
            </div>
          </div>
          <button className="btn btn-primary btn-lg" disabled={!prompt.trim() || busy !== null}
            onClick={() => start(name, timeframe, { prompt: prompt.trim(), parsed: checkedParse() }, 'prompt')}>
            {busy === 'prompt' ? <Spinner /> : null} Write the rules
          </button>
        </div>
        {error && <div className="notice notice-neg">{error}</div>}
      </section>

      {catalog.research && (
        <FindStrategy instrument={instrument} timeframe={timeframe} onUse={(r, on) => {
          setPrompt(r.strategy)
          setFound({ name: r.name, rules: r.strategy, parsed: r.parsed, ...on })
          const box = document.getElementById('prompt')
          box?.scrollIntoView({ behavior: 'smooth', block: 'center' })
          box?.focus({ preventScroll: true })
        }} />
      )}

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
              <li key={r.id}><a href={`#/p/${r.id}`}>{r.name}</a> <span className="faint">· {r.instrument ?? 'US100'} {r.timeframe}</span></li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
