import { ColorType, CrosshairMode, LineStyle, type DeepPartial, type ChartOptions } from 'lightweight-charts'

/** Token values for canvas drawing (canvas can't read CSS variables, so read them once). */
export function tokens() {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return {
    bg: v('--surface'), text: v('--text'), text2: v('--text-2'), text3: v('--text-3'),
    border: v('--border'), borderStrong: v('--border-strong'),
    accent: v('--accent'), pos: v('--pos'), neg: v('--neg'), warn: v('--warn'),
    luck: v('--luck'), font: v('--font'),
  }
}

export function baseOptions(t = tokens()): DeepPartial<ChartOptions> {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: t.bg },
      textColor: t.text3,
      fontFamily: t.font,
      fontSize: 11,
      attributionLogo: false,
    },
    grid: { vertLines: { visible: false }, horzLines: { color: t.border } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: t.borderStrong, style: LineStyle.Solid, labelBackgroundColor: t.borderStrong },
      horzLine: { color: t.borderStrong, style: LineStyle.Solid, labelBackgroundColor: t.borderStrong },
    },
  }
}

/** With 0.4 alpha, from a #rrggbb token. */
export function alpha(hex: string, a: number): string {
  const n = parseInt(hex.replace('#', ''), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}
