// Walking a canonical spec to find what the trader can tweak, and immutable updates by path.

import type { Spec } from '../api/types'

export type Path = (string | number)[]
type Obj = Record<string, unknown>

export type BlockRef = { block: string; params: Obj; inputs?: Record<string, BlockRef>; output?: string | null }

/** One block with one set of params, wherever it's used. The same block with the same params is one
    concept (the 09:30-10:00 range is both the setup event and the stop level), so it's edited in one
    place and every use changes together. */
export type BlockSite = { paths: Path[]; ref: BlockRef; where: string[] }

function isObj(v: unknown): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isBlock(v: unknown): v is BlockRef {
  return isObj(v) && typeof v.block === 'string'
}

/** Every distinct block in the spec, with every place it's used ("Setup 1 · sweep", "Stop"). */
export function blockSites(spec: Spec): BlockSite[] {
  const sites = new Map<string, BlockSite>()

  function add(ref: BlockRef, path: Path, where: string) {
    const key = JSON.stringify([ref.block, ref.params])
    const site = sites.get(key)
    if (site) {
      site.paths.push(path)
      if (!site.where.includes(where)) site.where.push(where)
    } else {
      sites.set(key, { paths: [path], ref, where: [where] })
    }
    for (const [name, input] of Object.entries(ref.inputs ?? {})) {
      add(input, [...path, 'inputs', name], `${where} · ${name}`)
    }
  }

  function walk(v: unknown, path: Path, where: string) {
    if (isBlock(v)) return add(v, path, where)
    if (Array.isArray(v)) return v.forEach((x, i) => walk(x, [...path, i], where))
    if (isObj(v)) for (const [k, x] of Object.entries(v)) walk(x, [...path, k], where)
  }

  const setup = (spec.setup as Obj[] | undefined) ?? []
  setup.forEach((step, i) => walk(step.event, ['setup', i, 'event'], `Setup ${i + 1} · ${String(step.name)}`))
  walk(spec.filters, ['filters'], 'Filters')
  for (const section of ['entry', 'stop', 'target'] as const) {
    walk(spec[section], [section], section.charAt(0).toUpperCase() + section.slice(1))
  }
  return [...sites.values()]
}

/** Set one param on every use of a block. */
export function setBlockParam(spec: Spec, site: BlockSite, param: string, value: unknown): Spec {
  return site.paths.reduce((s, path) => setIn(s, [...path, 'params', param], value), spec)
}

/** The spec's own numbers a trader adjusts: windows between steps, entry/stop/target values, limits, costs. */
export type NumberField = { path: Path; label: string; min?: number; max?: number; step?: number; integer?: boolean; optional?: boolean }

export function numberFields(spec: Spec): NumberField[] {
  const out: NumberField[] = []
  const setup = (spec.setup as Obj[] | undefined) ?? []
  setup.forEach((_, i) => {
    if (i > 0) out.push({ path: ['setup', i, 'within'], label: `Step ${i + 1} within (bars)`, min: 1, max: 500, integer: true })
  })
  const entry = spec.entry as Obj
  if (entry?.type === 'limit') {
    out.push({ path: ['entry', 'offset_pts'], label: 'Limit offset (points)', min: -10000, max: 10000, step: 0.25 })
    out.push({ path: ['entry', 'expiry_bars'], label: 'Cancel limit after (bars)', min: 1, max: 500, integer: true })
  }
  const stop = spec.stop as Obj
  if (stop?.type === 'level') out.push({ path: ['stop', 'buffer_pts'], label: 'Stop buffer (points)', min: 0, max: 10000, step: 0.25 })
  if (stop?.type === 'atr') {
    out.push({ path: ['stop', 'multiple'], label: 'Stop (× ATR)', min: 0.05, max: 20, step: 0.25 })
    out.push({ path: ['stop', 'length'], label: 'Stop ATR length', min: 2, max: 500, integer: true })
  }
  if (stop?.type === 'points') out.push({ path: ['stop', 'points'], label: 'Stop (points)', min: 0.01, max: 10000, step: 1 })
  const target = spec.target as Obj
  if (target?.type === 'r_multiple') out.push({ path: ['target', 'r'], label: 'Target (R)', min: 0.1, max: 50, step: 0.25 })
  out.push({ path: ['limits', 'max_trades_per_day'], label: 'Max trades per day', min: 1, max: 50, integer: true })
  out.push({ path: ['limits', 'max_bars_held'], label: 'Exit after (bars)', min: 1, max: 5000, integer: true, optional: true })
  out.push({ path: ['slippage_pts'], label: 'Slippage per side (points)', min: 0, max: 1000, step: 0.25 })
  return out
}

export function getIn(obj: unknown, path: Path): unknown {
  return path.reduce<unknown>((v, k) => (v == null ? undefined : (v as Obj)[k as string]), obj)
}

export function setIn<T>(obj: T, path: Path, value: unknown): T {
  if (path.length === 0) return value as T
  const [head, ...rest] = path
  if (Array.isArray(obj)) {
    const copy = [...obj]
    copy[head as number] = setIn(copy[head as number], rest, value)
    return copy as T
  }
  const base = (isObj(obj) ? obj : {}) as Obj
  return { ...base, [head]: setIn(base[head as string], rest, value) } as T
}
