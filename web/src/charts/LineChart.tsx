import { ColorType, createChart, LineSeries, LineStyle } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type LineSeriesInput = {
  name: string;
  color: string;
  dash?: boolean;
  points: { time: string; value: number }[];
};

type Props = { series: LineSeriesInput[]; height: number; percent?: boolean };

/** 折线图（lightweight-charts）。time 为 YYYY-MM-DD。 */
export function LineChart({ series, height, percent }: Props) {
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
    for (const s of series) {
      const line = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: 2,
        lineStyle: s.dash ? LineStyle.Dashed : LineStyle.Solid,
        title: s.name,
        priceLineVisible: false,
      });
      line.setData(s.points);
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, percent]);

  return <div ref={ref} style={{ height }} />;
}
