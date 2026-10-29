// What the bundled example strategies do, in words, for the home page.
export const EXAMPLES: Record<string, { title: string; text: string }> = {
  fvg_long: {
    title: 'Asia sweep, then FVG',
    text: 'NY AM killzone. Price sweeps the Asia low, then a bullish fair value gap within 10 bars. Limit at the gap top, stop under the last swing low, 2R.',
  },
  opening_range_long: {
    title: 'Opening range breakout',
    text: 'Once the 09:30–10:00 range is set, buy a close above its high within two hours. Stop at the range low, 1.5R.',
  },
  ema_cross_long: {
    title: '9/21 EMA cross',
    text: 'Long when the 9 EMA crosses above the 21 EMA in the NY AM or PM killzone. 30 point stop, 2R.',
  },
  displacement_short: {
    title: 'London displacement short',
    text: 'Tue–Thu in London, below the 50 EMA: short a bearish displacement candle. Stop 1.5 ATR, target the prior day low.',
  },
}
