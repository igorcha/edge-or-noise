import { useState } from 'react'
import { useCatalog } from '../lib/catalog'
import type { ParamSchema, Spec } from '../api/types'
import { humanize } from '../lib/format'
import { blockSites, getIn, numberFields, setBlockParam, setIn, type BlockSite, type NumberField } from '../lib/spec'
import { Segmented } from './ui'

const BLOCK_TITLES: Record<string, string> = {
  time_window: 'Time window', day_of_week: 'Days', window_high_low: 'Range high/low', swing: 'Swing',
  fvg: 'Fair value gap', sweep: 'Sweep', displacement: 'Displacement candle', ema: 'EMA', atr: 'ATR',
}
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri']

/** Every number and choice in the rules, editable. Each edit is re-checked by the server, and the run
    button says whether the result is a new trial. */
export function ParamEditor({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const catalog = useCatalog()
  const schemas = Object.fromEntries(catalog.catalog.blocks.map((b) => [b.name, b.params.properties]))
  const sites = blockSites(spec)
  return (
    <section className="params">
      <h2 className="eyebrow">Adjust</h2>
      <div className="param-group">
        <div className="param-row">
          <span className="field-label">Direction</span>
          <Segmented value={spec.direction} onChange={(v) => onChange({ ...spec, direction: v })}
            options={[{ value: 'long', label: 'Long' }, { value: 'short', label: 'Short' }]} />
        </div>
        {numberFields(spec).map((f) => (
          <NumberRow key={f.path.join('.')} field={f} value={getIn(spec, f.path) as number | null}
            onChange={(v) => onChange(setIn(spec, f.path, v))} />
        ))}
      </div>
      {sites.map((site) => (
        <BlockParams key={site.paths.map((p) => p.join('.')).join('|')} site={site} schema={schemas[site.ref.block] ?? {}}
          onChange={(param, v) => onChange(setBlockParam(spec, site, param, v))} />
      ))}
    </section>
  )
}

function BlockParams({ site, schema, onChange }: {
  site: BlockSite; schema: Record<string, ParamSchema>; onChange: (param: string, v: unknown) => void
}) {
  const params = site.ref.params ?? {}
  const custom = params.session === 'custom' || params.window === 'custom'
  const keys = Object.keys(schema).filter((k) => (k !== 'start' && k !== 'end') || custom)
  if (keys.length === 0) return null
  return (
    <div className="param-group">
      <div className="param-group-head">
        <span>{BLOCK_TITLES[site.ref.block] ?? humanize(site.ref.block)}</span>
        <span className="faint">{site.where.join(', ')}</span>
      </div>
      {keys.map((k) => (
        <ParamInput key={k} name={k} schema={schema[k]} value={params[k]} onChange={(v) => onChange(k, v)} />
      ))}
    </div>
  )
}

function options(s: ParamSchema): string[] {
  if (s.enum) return s.enum
  if (s.const) return [s.const]
  return (s.anyOf ?? []).flatMap(options)
}

function ParamInput({ name, schema, value, onChange }: { name: string; schema: ParamSchema; value: unknown; onChange: (v: unknown) => void }) {
  const label = humanize(name)
  if (schema.type === 'array') {
    const days = (value as string[]) ?? []
    return (
      <div className="param-row">
        <span className="field-label">{label}</span>
        <div className="chips">
          {DAYS.map((d) => {
            const on = days.includes(d)
            return (
              <button key={d} type="button" className={`chip${on ? ' on' : ''}`} aria-pressed={on}
                disabled={on && days.length === 1}
                onClick={() => onChange(on ? days.filter((x) => x !== d) : DAYS.filter((x) => x === d || days.includes(x)))}>
                {d.charAt(0).toUpperCase() + d.slice(1, 2)}
              </button>
            )
          })}
        </div>
      </div>
    )
  }
  const opts = options(schema)
  if (opts.length > 0) {
    return (
      <label className="param-row">
        <span className="field-label">{label}</span>
        <select className="select" value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {opts.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
        </select>
      </label>
    )
  }
  if (schema.type === 'integer' || schema.type === 'number') {
    return (
      <NumberRow field={{ path: [], label, min: schema.minimum ?? schema.exclusiveMinimum, max: schema.maximum,
        integer: schema.type === 'integer', step: schema.type === 'integer' ? 1 : 0.1 }}
        value={value as number} onChange={onChange} />
    )
  }
  return <TextRow label={label} value={(value as string | null) ?? ''} placeholder="HH:MM" onChange={(v) => onChange(v || null)} />
}

/** A number input that keeps what you type and only passes on valid numbers. */
function NumberRow({ field, value, onChange }: { field: NumberField; value: number | null; onChange: (v: number | null) => void }) {
  const [text, setText] = useState(value == null ? '' : String(value))
  const [shown, setShown] = useState(value)
  if (value !== shown) {  // the spec changed from elsewhere (another edit, a new parse): show it
    setShown(value)
    setText(value == null ? '' : String(value))
  }
  const n = Number(text)
  const valid = text.trim() === '' ? !!field.optional
    : Number.isFinite(n) && (!field.integer || Number.isInteger(n))
      && (field.min == null || n >= field.min) && (field.max == null || n <= field.max)
  return (
    <label className="param-row">
      <span className="field-label">{field.label}</span>
      <input className={`input num${valid ? '' : ' input-invalid'}`} inputMode="decimal" value={text}
        placeholder={field.optional ? 'off' : undefined}
        onChange={(e) => {
          const t = e.target.value
          setText(t)
          const x = Number(t)
          if (t.trim() === '' && field.optional) onChange(null)
          else if (t.trim() !== '' && Number.isFinite(x) && (!field.integer || Number.isInteger(x))
            && (field.min == null || x >= field.min) && (field.max == null || x <= field.max)) onChange(x)
        }} />
    </label>
  )
}

function TextRow({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (v: string) => void }) {
  const [text, setText] = useState(value)
  const [shown, setShown] = useState(value)
  if (value !== shown) {
    setShown(value)
    setText(value)
  }
  return (
    <label className="param-row">
      <span className="field-label">{label}</span>
      <input className="input num" value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)}
        onBlur={() => text !== value && onChange(text)} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
    </label>
  )
}
