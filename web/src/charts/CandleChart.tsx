import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries, type SeriesMarker, type Time } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export type Candle = { date: string; open: number; high: number; low: number; close: number; volume: number };

const MA_COLORS = ["#f5a623", "#22d3ee", "#a855f7", "#e879f9"];
const UP = "#22c55e";
const DOWN = "#ef4444";

export type TradeMark = { date: string; side: "BUY" | "SELL" };

type Props = {
  bars: Candle[];
  height: number;
  mas: number[];
  /** 持仓期（含两端），画浅色底色 */
  periods?: { start: string; end: string }[];
  trades?: TradeMark[];
  earnings?: string[];
  /** 选中的日期（如点击成交记录），在图上高亮并滚动到附近 */
  focus?: string | null;
};

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
export function CandleChart({ bars, height, mas, periods = [], trades = [], earnings = [], focus = null }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [readout, setReadout] = useState<Candle | null>(null);

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
    if (periods.length) {
      const held = chart.addSeries(HistogramSeries, { priceScaleId: "held", lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("held").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
      held.setData(bars.map((b) => ({
        time: b.date, value: 1,
        color: periods.some((p) => p.start <= b.date && b.date <= p.end) ? "rgba(41,98,255,0.10)" : "rgba(0,0,0,0)",
      })));
    }
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
    const have = new Set(bars.map((b) => b.date));
    const marks: SeriesMarker<Time>[] = [
      ...trades.filter((t) => have.has(t.date)).map((t) => ({
        time: t.date, position: t.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
        shape: t.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
        color: t.date === focus ? "#ffffff" : t.side === "BUY" ? UP : DOWN, text: t.side === "BUY" ? "B" : "S",
      })),
      ...earnings.filter((d) => have.has(d)).map((d) => ({ time: d, position: "aboveBar" as const, shape: "circle" as const, color: "#f5a623", text: "E" })),
    ].sort((a, b) => String(a.time).localeCompare(String(b.time)));
    if (marks.length) createSeriesMarkers(candles, marks);
    const byDate = new Map(bars.map((b) => [b.date, b]));
    chart.subscribeCrosshairMove((param) => setReadout(param.time ? (byDate.get(String(param.time)) ?? null) : null));
    chart.timeScale().fitContent();
    if (focus && have.has(focus)) {
      const i = bars.findIndex((b) => b.date === focus);
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, i - 60), to: Math.min(bars.length, i + 60) });
    }
    return () => chart.remove();
  }, [bars, height, mas, periods, trades, earnings, focus]);

  const last = readout ?? bars.at(-1) ?? null;
  return (
    <div style={{ position: "relative" }}>
      {last && (
        <div className="readout num sm">
          {last.date} 开 {last.open.toFixed(2)} 高 {last.high.toFixed(2)} 低 {last.low.toFixed(2)} 收 {last.close.toFixed(2)}
        </div>
      )}
      <div ref={ref} style={{ height }} />
    </div>
  );
}
