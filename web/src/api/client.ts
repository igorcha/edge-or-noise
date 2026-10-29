import type {
  BarsWindow, Catalog, ParseResult, Preview, Project, RunResult, Spec, Trial, TrialSummary,
} from './types'

/** An API error, with the spec path when the engine's validator pointed at a field. */
export class ApiError extends Error {
  status: number
  path: string | null

  constructor(status: number, message: string, path: string | null = null) {
    super(message)
    this.status = status
    this.path = path
  }
}

async function request<T>(method: 'GET' | 'POST', url: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${url}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, "Can't reach the server.")
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(data, res.status), data?.path ?? null)
  }
  return data as T
}

function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    if (typeof d.error === 'string') return d.error
    if (typeof d.detail === 'string') return d.detail
    const first = Object.entries(d)[0]
    if (first) return `${first[0]}: ${Array.isArray(first[1]) ? first[1].join(' ') : String(first[1])}`
  }
  return `Request failed (${status}).`
}

export const api = {
  catalog: () => request<Catalog>('GET', '/catalog'),
  demo: () => request<Project>('GET', '/demo'),
  createProject: (body: { name: string; instrument: string; timeframe: string }) =>
    request<Project>('POST', '/projects', body),
  project: (id: number) => request<Project>('GET', `/projects/${id}`),
  trials: (id: number) => request<TrialSummary[]>('GET', `/projects/${id}/trials`),
  trial: (id: number) => request<Trial>('GET', `/trials/${id}`),
  check: (id: number, spec: Spec) => request<Preview>('POST', `/projects/${id}/check`, { spec }),
  run: (id: number, spec: Spec) => request<RunResult>('POST', `/projects/${id}/trials`, { spec }),
  parse: (id: number, prompt: string, answers: { question: string; answer: string }[] = []) =>
    request<ParseResult>('POST', `/projects/${id}/parse`, { prompt, answers }),
  exam: (id: number, trial?: number) =>
    request<Trial & { project: Project }>('POST', `/projects/${id}/exam`, trial ? { trial } : {}),
  bars: (trial: number, trade: number, before = 60, after = 20) =>
    request<BarsWindow>('GET', `/bars?trial=${trial}&trade=${trade}&before=${before}&after=${after}`),
}
