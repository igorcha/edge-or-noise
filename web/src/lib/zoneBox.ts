import type { CanvasRenderingTarget2D } from 'fancy-canvas'
import type {
  IChartApiBase, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter,
  SeriesType, Time,
} from 'lightweight-charts'

export type Zone = { from: Time; to: Time; top: number; bottom: number; fill: string; stroke: string }

/** A price zone over a time span (an FVG from its first candle to the trade's exit), drawn under the candles. */
export class ZoneBox implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null
  private series: ISeriesApi<SeriesType, Time> | null = null
  private readonly zone: Zone

  constructor(zone: Zone) {
    this.zone = zone
  }

  attached({ chart, series, requestUpdate }: SeriesAttachedParameter<Time>) {
    this.chart = chart
    this.series = series
    requestUpdate()
  }

  detached() {
    this.chart = null
    this.series = null
  }

  paneViews(): readonly IPrimitivePaneView[] {
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) }
    return [{ zOrder: () => 'bottom', renderer: () => renderer }]
  }

  private draw(target: CanvasRenderingTarget2D) {
    if (!this.chart || !this.series) return
    const ts = this.chart.timeScale()
    const x1 = ts.timeToCoordinate(this.zone.from)
    const x2 = ts.timeToCoordinate(this.zone.to)
    const y1 = this.series.priceToCoordinate(this.zone.top)
    const y2 = this.series.priceToCoordinate(this.zone.bottom)
    if (x1 === null || x2 === null || y1 === null || y2 === null) return
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const left = Math.round(Math.min(x1, x2) * hr)
      const top = Math.round(Math.min(y1, y2) * vr)
      const w = Math.max(Math.round(Math.abs(x2 - x1) * hr), 1)
      const h = Math.max(Math.round(Math.abs(y2 - y1) * vr), 1)
      ctx.fillStyle = this.zone.fill
      ctx.fillRect(left, top, w, h)
      ctx.strokeStyle = this.zone.stroke
      ctx.lineWidth = Math.max(1, Math.floor(hr))
      ctx.strokeRect(left + 0.5, top + 0.5, w - 1, h - 1)
    })
  }
}
