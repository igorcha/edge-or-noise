import { useEffect, useRef } from 'react'
import type { Trade } from '../api/types'
import { fmtR } from '../lib/format'
import { nyDate } from '../lib/time'

const REASONS: Record<Trade['exit_reason'], string> = { target: 'Target', stop: 'Stop', time: 'Time', end: 'Open at end' }

/** Every trade, newest last. The list is the index: pick one to see it on the chart. */
export function TradeList({ trades, selected, onSelect }: { trades: Trade[]; selected: number; onSelect: (i: number) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const body = useRef<HTMLTableSectionElement>(null)
  const first = useRef(true)

  useEffect(() => {  // keep the picked trade in view inside the list (never scroll the page) when picked elsewhere
    if (first.current) { first.current = false; return }
    const row = body.current?.children[selected] as HTMLElement | undefined
    const scroller = box.current
    if (!row || !scroller) return
    const head = scroller.querySelector('thead')?.getBoundingClientRect().height ?? 0
    if (row.offsetTop - head < scroller.scrollTop) scroller.scrollTop = row.offsetTop - head
    else if (row.offsetTop + row.offsetHeight > scroller.scrollTop + scroller.clientHeight) {
      scroller.scrollTop = row.offsetTop + row.offsetHeight - scroller.clientHeight
    }
  }, [selected])

  return (
    <section className="card trade-list">
      <div className="table-scroll" ref={box}>
        <table>
          <thead>
            <tr>
              <th className="num">#</th><th>Entry (NY)</th><th>Side</th><th className="num">Entry</th><th className="num">Exit</th>
              <th>Exit</th><th className="num">R</th><th className="num">Worst</th><th className="num">Best</th><th className="num">Bars</th>
            </tr>
          </thead>
          <tbody ref={body}>
            {trades.map((t, i) => (
              <tr key={i} className={i === selected ? 'on' : ''} onClick={() => onSelect(i)} tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSelect(i)
                  if (e.key === 'ArrowDown' && i + 1 < trades.length) { e.preventDefault(); onSelect(i + 1) }
                  if (e.key === 'ArrowUp' && i > 0) { e.preventDefault(); onSelect(i - 1) }
                }}>
                <td className="num faint">{i + 1}</td>
                <td className="date">{nyDate(t.entry_time)}</td>
                <td>{t.direction === 1 ? 'Long' : 'Short'}</td>
                <td className="num">{t.entry.toFixed(2)}</td>
                <td className="num">{t.exit.toFixed(2)}</td>
                <td className="muted">{REASONS[t.exit_reason]}</td>
                <td className={`num ${t.R > 0 ? 'pos' : t.R < 0 ? 'neg' : ''}`}>{fmtR(t.R)}</td>
                <td className="num faint">{t.mae_R.toFixed(2)}</td>
                <td className="num faint">{t.mfe_R.toFixed(2)}</td>
                <td className="num faint">{t.bars_held}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
