import { ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries, LineStyle, PriceScaleMode } from "lightweight-charts";
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
  /** 标记的文字（默认“调仓”） */
  markerText?: string;
  /** 对数纵轴（净值对比） */
  logScale?: boolean;
  /** 从该日期（YYYY-MM-DD）起加浅蓝底色，如样本外区间 */
  shadeFrom?: string;
};

/** 折线图（lightweight-charts）。time 为 YYYY-MM-DD。 */
export function LineChart({ series, height, percent, markers, markerText = "调仓", logScale, shadeFrom }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      height,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#787b86", fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: "#1a1e29" }, horzLines: { color: "#1d212b" } },
      rightPriceScale: { borderColor: "#232733", mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
      // minBarSpacing 调小：十年日线（约 2500 点）也能完整放进图宽，不截掉前几年
      timeScale: { borderColor: "#232733", minBarSpacing: 0.01 },
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
          markers.filter((t) => have.has(t)).map((t) => ({ time: t, position: "aboveBar", color: "#f5a623", shape: "arrowDown", text: markerText })),
        );
      }
    });
    if (shadeFrom && series[0]) {
      const band = chart.addSeries(HistogramSeries, {
        priceScaleId: "shade", color: "rgba(41,98,255,0.06)", lastValueVisible: false, priceLineVisible: false,
      });
      chart.priceScale("shade").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
      band.setData(series[0].points.filter((p) => p.time >= shadeFrom).map((p) => ({ time: p.time, value: 1 })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, percent, markers, markerText, logScale, shadeFrom]);

  return <div ref={ref} style={{ height }} />;
}
