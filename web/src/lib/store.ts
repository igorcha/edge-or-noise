// Per-browser conveniences only: which projects this browser made, and how a new one should start.
// Everything that matters (trials, the counter, the exam) lives on the server.

export type Recent = { id: number; name: string; timeframe: string; at: string }
export type Start = { example?: string; prompt?: string }

function read<T>(key: string, fallback: T, storage: () => Storage = () => localStorage): T {
  try {
    const raw = storage().getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown, storage: () => Storage = () => localStorage) {
  try {
    storage().setItem(key, JSON.stringify(value))
  } catch {
    // private mode or storage full: the app works without it
  }
}

export function recentProjects(): Recent[] {
  return read<Recent[]>('recent-projects', [])
}

export function rememberProject(p: Omit<Recent, 'at'>) {
  const rest = recentProjects().filter((r) => r.id !== p.id)
  write('recent-projects', [{ ...p, at: new Date().toISOString() }, ...rest].slice(0, 12))
}

export function setStart(projectId: number, start: Start) {
  write(`start:${projectId}`, start, () => sessionStorage)
}

export function takeStart(projectId: number): Start | null {
  const s = read<Start | null>(`start:${projectId}`, null, () => sessionStorage)
  try {
    sessionStorage.removeItem(`start:${projectId}`)
  } catch {
    // ignore
  }
  return s
}
