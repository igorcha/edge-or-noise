import { useCallback, useEffect, useRef, useState } from 'react'
import { useCatalog } from '../lib/catalog'
import { ApiError, api } from '../api/client'
import type { Preview, Project, Spec, Trial, TrialSummary } from '../api/types'
import { rememberProject, takeStart, type Start } from '../lib/store'
import { RulesPanel } from './RulesPanel'
import { Results } from './Results'
import { TopBar } from './TopBar'
import { Spinner } from './ui'

export type Draft = { spec: Spec | null; preview: Preview | null; error: ApiError | null; checking: boolean }

// How a new project starts (an example or a prompt), read once: React may call initializers twice.
const starts = new Map<number, Start | null>()
function startOf(projectId: number): Start | null {
  if (!starts.has(projectId)) starts.set(projectId, takeStart(projectId))
  return starts.get(projectId) ?? null
}

export function Workspace({ projectId }: { projectId: number }) {
  const catalog = useCatalog()
  const [project, setProject] = useState<Project | null>(null)
  const [trials, setTrials] = useState<TrialSummary[]>([])
  const [selected, setSelected] = useState<Trial | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>({ spec: null, preview: null, error: null, checking: false })
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [start] = useState(() => startOf(projectId))  // fixed for this mount, even as the cache below is cleared
  const checkSeq = useRef(0)

  useEffect(() => () => { starts.delete(projectId) }, [projectId])  // consumed: coming back later starts clean

  const refresh = useCallback(async () => {
    const [p, ts] = await Promise.all([api.project(projectId), api.trials(projectId)])
    setProject(p)
    setTrials(ts)
    return { p, ts }
  }, [projectId])

  const selectTrial = useCallback(async (id: number) => {
    setSelected(await api.trial(id))
  }, [])

  // The draft is checked (validated, card rendered, trial number found) shortly after every edit.
  const edit = useCallback((spec: Spec | null) => {
    setDraft((d) => ({ ...d, spec, checking: spec !== null }))
  }, [])

  useEffect(() => {
    if (!draft.spec || !draft.checking) return
    const seq = ++checkSeq.current
    const t = setTimeout(() => {
      api.check(projectId, draft.spec!).then(
        // adopt the canonical spec: every default explicit, so the editor shows every parameter
        (preview) => seq === checkSeq.current && setDraft((d) => ({ ...d, spec: preview.spec, preview, error: null, checking: false })),
        (error: ApiError) => seq === checkSeq.current && setDraft((d) => ({ ...d, error, checking: false })),
      )
    }, 250)
    return () => clearTimeout(t)
  }, [draft.spec, draft.checking, projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- state is set after the fetch resolves, not synchronously
    refresh().then(async ({ p, ts }) => {
      rememberProject({ id: p.id, name: p.name, instrument: p.instrument, timeframe: p.timeframe })
      const latest = ts.at(-1)
      if (latest) {
        const t = await api.trial(latest.id)
        setSelected(t)
        if (!start) edit(ts.filter((x) => x.stage === 'development').length ? t.spec : null)
      }
      if (start?.example && catalog.examples[start.example]) {
        edit({ ...catalog.examples[start.example], timeframe: p.timeframe })
      }
    }, (e: Error) => setLoadError(e.message))
  }, [refresh, start, catalog, edit])

  async function run() {
    if (!project || !draft.spec) return
    setRunning(true)
    setRunError(null)
    try {
      const result = await api.run(project.id, draft.spec)
      setSelected(result)
      await refresh()
      edit(draft.spec)  // re-check: the server now knows these rules as a trial
    } catch (e) {
      setRunError((e as Error).message)
    } finally {
      setRunning(false)
    }
  }

  async function exam(trialId: number) {
    const result = await api.exam(projectId, trialId)
    setSelected(result)
    await refresh()
  }

  if (loadError) return <div className="center-screen"><div className="notice notice-neg">{loadError}</div></div>
  if (!project) return <div className="center-screen faint"><Spinner /></div>

  return (
    <div className="workspace">
      <TopBar project={project} trials={trials} />
      <aside className="panel">
        <RulesPanel project={project} draft={draft} onEdit={edit} running={running} runError={runError}
          onRun={run} onView={(n) => { const t = trials.find((x) => x.number === n && x.stage === 'development'); if (t) selectTrial(t.id) }}
          initialPrompt={start?.prompt ?? null} initialParsed={start?.parsed ?? null} />
      </aside>
      <main className="main">
        <Results project={project} trials={trials} selected={selected} onSelect={selectTrial}
          onEditFrom={(spec) => edit(spec)} onExam={exam} running={running} />
      </main>
    </div>
  )
}
