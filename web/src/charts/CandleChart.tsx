import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type Candle = { date: string; open: number; high: number; low: number; close: number; volume: number };

const MA_COLORS = ["#f5a623", "#22d3ee", "#a855f7", "#e879f9"];
const UP = "#22c55e";
const DOWN = "#ef4444";

type Props = { bars: Candle[]; height: number; mas: number[] };

function sma(bars: Candle[], win: number) {
  const out: { time: string; value: number }[] = [];
  let sum = 0;
  bars.forEach((b, i) => {
    sum += b.close;
    if (i >= win) sum -= bars[i - win].close;
    if (i >= win - 1) out.push({ time: b.date, value: sum / win });
  });
  return out;
}

/** K 线 + 成交量 + 均线（绿涨红跌）。 */
export function CandleChart({ bars, height, mas }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      height,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#787b86", fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: "#1a1e29" }, horzLines: { color: "#1d212b" } },
      rightPriceScale: { borderColor: "#232733" },
      timeScale: { borderColor: "#232733" },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN,
    });
    candles.setData(bars.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })));
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.25 } });
    vol.setData(bars.map((b) => ({ time: b.date, value: b.volume, color: b.close >= b.open ? "rgba(34,197,94,.4)" : "rgba(239,68,68,.4)" })));
    mas.forEach((w, i) => {
      chart.addSeries(LineSeries, { color: MA_COLORS[i % MA_COLORS.length], lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
        .setData(sma(bars, w));
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, height, mas]);

  return <div ref={ref} style={{ height }} />;
}
