import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { Catalog } from './api/types'
import { Home } from './components/Home'
import { Workspace } from './components/Workspace'
import { Spinner } from './components/ui'
import { CatalogContext } from './lib/catalog'

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const onChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return hash
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hash = useHash()

  useEffect(() => {
    api.catalog().then(setCatalog, (e: Error) => setError(e.message))
  }, [])

  if (error) return <div className="center-screen"><div className="notice notice-neg">{error}</div></div>
  if (!catalog) return <div className="center-screen faint"><Spinner /></div>

  const project = /^#\/p\/(\d+)/.exec(hash)
  return (
    <CatalogContext.Provider value={catalog}>
      {project ? <Workspace key={project[1]} projectId={Number(project[1])} /> : <Home />}
    </CatalogContext.Provider>
  )
}
