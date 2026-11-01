import { ColorType, createChart, createSeriesMarkers, LineSeries, LineStyle } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type LineSeriesInput = {
  name: string;
  color: string;
  dash?: boolean;
  points: { time: string; value: number }[];
};

type Props = {
  series: LineSeriesInput[];
  height: number;
  percent?: boolean;
  /** 在第一条线上标出的日期（如调仓日），YYYY-MM-DD */
  markers?: string[];
};

/** 折线图（lightweight-charts）。time 为 YYYY-MM-DD。 */
export function LineChart({ series, height, percent, markers }: Props) {
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
      localization: percent ? { priceFormatter: (v: number) => `${(v * 100).toFixed(1)}%` } : undefined,
    });
    series.forEach((s, i) => {
      const line = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: 2,
        lineStyle: s.dash ? LineStyle.Dashed : LineStyle.Solid,
        title: s.name,
        priceLineVisible: false,
      });
      line.setData(s.points);
      if (i === 0 && markers?.length) {
        const have = new Set(s.points.map((p) => p.time));
        createSeriesMarkers(
          line,
          markers.filter((t) => have.has(t)).map((t) => ({ time: t, position: "aboveBar", color: "#f5a623", shape: "arrowDown", text: "调仓" })),
        );
      }
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, percent, markers]);

  return <div ref={ref} style={{ height }} />;
}
