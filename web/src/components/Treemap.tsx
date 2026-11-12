import { Link } from "react-router";
import { type Rect, squarify } from "../lib/treemap";

export type TreeCell = { key: string; label: string; sublabel?: string; value: number; color: string; title: string; to?: { pathname: string; search: string } };
export type TreeGroup = { key: string; label: string; cells: TreeCell[] };

/** 按组（如板块）两级布局的矩形树图；格子可点击跳转。 */
export function Treemap({ groups, width, height }: { groups: TreeGroup[]; width: number; height: number }) {
  const sorted = [...groups].map((g) => ({ ...g, cells: [...g.cells].sort((a, b) => b.value - a.value) }))
    .sort((a, b) => sum(b) - sum(a));
  const outer = squarify(sorted.map(sum), { x: 0, y: 0, w: width, h: height });
  return (
    <div className="treemap" style={{ width: "100%", height, position: "relative" }}>
      {sorted.map((g, gi) => {
        const r = outer[gi];
        const inner = squarify(g.cells.map((c) => c.value), { x: r.x, y: r.y + 14, w: r.w, h: Math.max(0, r.h - 14) });
        return (
          <div key={g.key}>
            <div className="tm-group" style={pct(r, width, height, 14)}>{g.label}</div>
            {g.cells.map((c, ci) => {
              const cr = inner[ci];
              const style = { ...pct(cr, width, height), background: c.color };
              const body = cr.w > 34 && cr.h > 18 ? (
                <span><b>{c.label}</b>{c.sublabel && cr.w > 52 && cr.h > 34 && <span className="tm-sub">{c.sublabel}</span>}</span>
              ) : null;
              return c.to ? (
                <Link key={c.key} to={c.to} className="tm-cell" style={style} title={c.title}>{body ?? <span className="sr">{c.label}</span>}</Link>
              ) : (
                <div key={c.key} className="tm-cell" style={style} title={c.title}>{body ?? <span className="sr">{c.label}</span>}</div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

function sum(g: TreeGroup): number {
  return g.cells.reduce((a, c) => a + c.value, 0);
}

function pct(r: Rect, w: number, h: number, fixedH?: number) {
  return {
    position: "absolute" as const,
    left: `${(r.x / w) * 100}%`,
    top: `${(r.y / h) * 100}%`,
    width: `${(r.w / w) * 100}%`,
    height: fixedH ?? `${(r.h / h) * 100}%`,
  };
}
