export type DonutPart = { label: string; value: number; color: string };

/** SVG 环形图；value 为占比（0–1），合计不足 1 的部分留空。 */
export function Donut({ parts, size = 120 }: { parts: DonutPart[]; size?: number }) {
  const r = size / 2 - 10;
  const c = 2 * Math.PI * r;
  const lens = parts.map((p) => Math.max(0, p.value) * c);
  const starts = lens.map((_, i) => lens.slice(0, i).reduce((a, b) => a + b, 0));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="分仓占比">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#1d212b" strokeWidth={14} />
      {parts.map((p, i) => {
        const len = lens[i];
        return (
          <circle
            key={p.label}
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={p.color}
            strokeWidth={14}
            strokeDasharray={`${len} ${c - len}`}
            strokeDashoffset={-starts[i]}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        );
      })}
    </svg>
  );
}
