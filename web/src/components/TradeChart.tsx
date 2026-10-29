import {
  CandlestickSeries, LineSeries, LineStyle, createChart, createSeriesMarkers, type SeriesMarker, type Time, type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { BarsWindow, Trade, Trial } from '../api/types'
import { alpha, baseOptions, tokens } from '../lib/chart'
import { fmtR } from '../lib/format'
import { nyTime } from '../lib/time'
import { ZoneBox } from '../lib/zoneBox'
import { LockIcon, Spinner } from './ui'

/** The bars around one trade: setup steps, entry and exit, stop and target for the life of the trade,
    and any zone the setup used (an FVG box from its first candle to the exit). Holdout bars never load. */
export function TradeChart({ trial, index }: { trial: Trial; index: number }) {
  if (trial.stage === 'holdout') return <HoldoutTrade trial={trial} index={index} />
  return <BarsChart trial={trial} index={index} />
}

/** Exam trades are reported, never charted: the holdout's bars stay sealed even after the exam. */
function HoldoutTrade({ trial, index }: { trial: Trial; index: number }) {
  const tr = trial.trades[index]
  return (
    <section className="card chart-card">
      <div className="chart-head">
        <div>
          <h3 className="chart-title">Trade {index + 1} <span className={tr.R > 0 ? 'pos' : 'neg'}>{fmtR(tr.R)}</span></h3>
          <p className="faint">{nyTime(Date.parse(tr.entry_time) / 1000, true)} NY · {tr.exit_reason} after {tr.bars_held} bars · worst {tr.mae_R.toFixed(2)}R, best {tr.mfe_R.toFixed(2)}R</p>
        </div>
        <span className="badge"><LockIcon /> Holdout bars stay hidden</span>
      </div>
    </section>
  )
}

function BarsChart({ trial, index }: { trial: Trial; index: number }) {
  const [win, setWin] = useState<BarsWindow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    api.bars(trial.id, index, 40, 24).then(
      (w) => { if (live) { setWin(w); setError(null) } },
      (e: ApiError) => { if (live) setError(e.status === 403 ? 'Holdout bars stay hidden: the exam reports results, not charts.' : e.message) },
    )
    return () => { live = false }
  }, [trial.id, index])

  useEffect(() => {
    if (!box.current || !win) return
    const t = tokens()
    const chart = createChart(box.current, {
      ...baseOptions(t),
      localization: { timeFormatter: (x: Time) => nyTime(x as number, true) },
      timeScale: { borderVisible: false, timeVisible: true, tickMarkFormatter: (x: Time) => nyTime(x as number) },
    })
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: t.text2, downColor: t.luck, borderVisible: false, wickUpColor: t.text3, wickDownColor: t.text3,
      priceLineVisible: false, lastValueVisible: false,
    })
    const time = (bar: number) => win.bars[Math.min(Math.max(bar - win.first_bar, 0), win.bars.length - 1)].time as UTCTimestamp
    candles.setData(win.bars.map((b) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })))

    const tr = win.trade
    const from = time(tr.entry_bar)
    const to = time(tr.exit_bar)
    const span = (price: number, color: string, style: LineStyle) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
      s.setData(from === to ? [{ time: from, value: price }] : [{ time: from, value: price }, { time: to, value: price }])
    }
    span(tr.stop, t.neg, LineStyle.Dashed)
    span(tr.target, t.pos, LineStyle.Dashed)
    span(tr.entry, t.text2, LineStyle.Solid)

    for (const step of steps(tr)) {
      const top = tr[`${step}.top`], bottom = tr[`${step}.bottom`]
      if (typeof top === 'number' && typeof bottom === 'number') {
        // a zone from the setup (an FVG): from its first candle (two before the one it formed on) to the exit
        candles.attachPrimitive(new ZoneBox({ from: time((tr[`${step}.bar`] as number) - 2), to, top, bottom,
          fill: alpha(t.accent, 0.14), stroke: alpha(t.accent, 0.5) }))
      }
    }

    const long = tr.direction === 1
    const entry: SeriesMarker<Time> = {
      time: from, position: long ? 'belowBar' : 'aboveBar', shape: long ? 'arrowUp' : 'arrowDown', color: t.accent, text: long ? 'Buy' : 'Sell',
    }
    const exit: SeriesMarker<Time> = {
      time: to, position: long ? 'aboveBar' : 'belowBar', shape: 'square', color: tr.R > 0 ? t.pos : t.neg, text: fmtR(tr.R, 1), size: 0.8,
    }
    const markers = [
      ...steps(tr).map((step): SeriesMarker<Time> => ({
        // on the side away from the entry arrow, so the labels don't collide when the steps are close
        time: time(tr[`${step}.bar`] as number), position: long ? 'aboveBar' : 'belowBar', shape: 'circle', color: t.text3, text: step, size: 0.6,
      })),
      entry, exit,
    ].sort((a, b) => (a.time as number) - (b.time as number))
    createSeriesMarkers(candles, markers)
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [win])

  const tr: Trade | undefined = trial.trades[index]
  return (
    <section className="card chart-card">
      <div className="chart-head">
        <div>
          <h3 className="chart-title">Trade {index + 1} <span className={tr && tr.R > 0 ? 'pos' : 'neg'}>{tr ? fmtR(tr.R) : ''}</span></h3>
          {tr && <p className="faint">{nyTime(Date.parse(tr.entry_time) / 1000, true)} NY · {tr.exit_reason} after {tr.bars_held} bars · worst {tr.mae_R.toFixed(2)}R, best {tr.mfe_R.toFixed(2)}R</p>}
        </div>
        <div className="legend">
          <span><i className="swatch stop" /> Stop</span>
          <span><i className="swatch target" /> Target</span>
          <span><i className="swatch zone" /> Setup zone</span>
        </div>
      </div>
      {error ? <div className="chart-box trade-box chart-msg faint">{error}</div>
        : win ? <div className="chart-box trade-box" ref={box} />
          : <div className="chart-box trade-box chart-msg faint"><Spinner /></div>}
    </section>
  )
}

function steps(tr: Trade): string[] {
  return Object.keys(tr).filter((k) => k.endsWith('.bar') && typeof tr[k] === 'number').map((k) => k.slice(0, -4))
}
