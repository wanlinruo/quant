/** 迷你走势图（SVG 折线）；涨为绿、跌为红。 */
export function Sparkline({ values, width = 80, height = 22 }: { values: (number | null)[]; width?: number; height?: number }) {
  const valid = values.filter((v): v is number => v != null);
  if (valid.length < 2) return <span className="muted">—</span>;
  const min = Math.min(...valid);
  const max = Math.max(...valid);
  const span = max - min || 1;
  const segments: string[][] = [[]];
  values.forEach((v, i) => {
    if (v == null) {
      if (segments.at(-1)?.length) segments.push([]);
      return;
    }
    segments[segments.length - 1].push(`${(i / (values.length - 1)) * width},${height - ((v - min) / span) * height}`);
  });
  const up = valid[valid.length - 1] >= valid[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      {segments.filter((pts) => pts.length > 1).map((pts, i) => (
        <polyline key={i} points={pts.join(" ")} fill="none" stroke={up ? "#22c55e" : "#ef4444"} strokeWidth={1.2} />
      ))}
    </svg>
  );
}
