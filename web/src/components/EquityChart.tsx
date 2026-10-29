import {
  AreaSeries, LineSeries, LineStyle, createChart, createSeriesMarkers, type ISeriesMarkersPluginApi, type Time, type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { Trial } from '../api/types'
import { alpha, baseOptions, tokens } from '../lib/chart'
import { fmtR } from '../lib/format'

/** Cumulative R by trade number, over the band of what random entries with the same exits earned
    (5th to 95th percentile of the baseline runs). A curve inside the band is what luck does. */
export function EquityChart({ trial, selected, onPick }: { trial: Trial; selected: number | null; onPick: (i: number) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const pick = useRef(onPick)
  useEffect(() => { pick.current = onPick }, [onPick])

  useEffect(() => {
    if (!box.current) return
    const t = tokens()
    const chart = createChart(box.current, {
      ...baseOptions(t),
      localization: { timeFormatter: (x: Time) => `Trade ${x as number}`, priceFormatter: (p: number) => fmtR(p, 1) },
      timeScale: { borderVisible: false, tickMarkFormatter: (x: Time) => `${x as number}`, fixLeftEdge: true, fixRightEdge: true },
      handleScroll: false,
      handleScale: false,
    })
    const x = (i: number) => (i + 1) as UTCTimestamp
    const { p5, p50, p95 } = trial.baseline.curves
    const flat = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }
    // The band: an area down to the bottom from p95, covered below p5 by an area in the background colour.
    chart.addSeries(AreaSeries, { ...flat, lineVisible: false, topColor: alpha(t.luck, 0.32), bottomColor: alpha(t.luck, 0.32) })
      .setData(p95.map((v, i) => ({ time: x(i), value: v })))
    chart.addSeries(AreaSeries, { ...flat, lineVisible: false, topColor: t.bg, bottomColor: t.bg })
      .setData(p5.map((v, i) => ({ time: x(i), value: v })))
    chart.addSeries(LineSeries, { ...flat, color: t.text3, lineWidth: 1, lineStyle: LineStyle.Dashed })
      .setData(p50.map((v, i) => ({ time: x(i), value: v })))
    let eq = 0
    const equity = chart.addSeries(LineSeries, {
      color: t.accent, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerRadius: 4, crosshairMarkerBackgroundColor: t.accent,
    })
    equity.setData(trial.trades.map((tr, i) => ({ time: x(i), value: (eq += tr.R) })))
    equity.createPriceLine({ price: 0, color: t.borderStrong, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false })
    markers.current = createSeriesMarkers(equity, [])
    chart.timeScale().fitContent()
    chart.subscribeClick((p) => {
      if (p.time !== undefined) pick.current((p.time as number) - 1)
    })
    return () => {
      chart.remove()
      markers.current = null
    }
  }, [trial])

  useEffect(() => {  // the trade open on the chart below, marked on the curve
    markers.current?.setMarkers(selected == null ? [] : [{
      time: (selected + 1) as UTCTimestamp, position: 'inBar', shape: 'circle', color: tokens().text, size: 0.6,
    }])
  }, [selected, trial])

  const last = trial.trades.reduce((a, tr) => a + tr.R, 0)
  return (
    <section className="card chart-card">
      <div className="chart-head">
        <div>
          <h3 className="chart-title">Equity against random entries</h3>
          <p className="faint">Click the curve to open that trade.</p>
        </div>
        <div className="legend">
          <span><i className="swatch line" /> This strategy <b className="num">{fmtR(last, 1)}</b></span>
          <span><i className="swatch band" /> Random entries, 5–95%</span>
          <span><i className="swatch dash" /> Median</span>
        </div>
      </div>
      <div className="chart-box equity-box" ref={box} />
    </section>
  )
}
