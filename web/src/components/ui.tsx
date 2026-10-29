import type { ReactNode } from 'react'
import type { Tone } from '../lib/verdict'

export function Badge({ tone, dot, children, title }: { tone?: Tone | 'accent'; dot?: boolean; children: ReactNode; title?: string }) {
  return (
    <span className={`badge${tone ? ` badge-${tone}` : ''}`} title={title}>
      {dot && <span className="dot" />}
      {children}
    </span>
  )
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />
}

export function Segmented<T extends string>({ value, options, onChange, disabled }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean
}) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} disabled={disabled}
          className={o.value === value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function LockIcon({ open = false }: { open?: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="3" y="7" width="10" height="7.5" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
      <path d={open ? 'M5.5 7V5a2.5 2.5 0 0 1 4.9-.7' : 'M5.5 7V5a2.5 2.5 0 0 1 5 0v2'} stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

export function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="var(--surface-3)" />
      <path d="M6 21 L12 15 L17 18 L26 9" fill="none" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M6 25 H26" stroke="var(--border-strong)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}
