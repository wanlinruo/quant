export type Rect = { x: number; y: number; w: number; h: number };

function worst(areas: number[], side: number): number {
  const s = areas.reduce((a, b) => a + b, 0);
  const max = Math.max(...areas);
  const min = Math.min(...areas);
  if (min <= 0 || s <= 0 || side <= 0) return Infinity;
  return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
}

/** squarified 矩形树图布局：返回与 values 同顺序的矩形，面积与值成比例、铺满 rect。 */
export function squarify(values: number[], rect: Rect): Rect[] {
  const n = values.length;
  const total = values.reduce((a, b) => a + Math.max(0, b), 0);
  if (n === 0) return [];
  if (total <= 0) return values.map(() => ({ x: rect.x, y: rect.y, w: 0, h: 0 }));
  const scale = (rect.w * rect.h) / total;
  const areas = values.map((v) => Math.max(0, v) * scale);
  const out: Rect[] = new Array(n);
  let { x, y, w, h } = rect;
  let i = 0;
  while (i < n) {
    const side = Math.min(w, h);
    const row = [i];
    let best = worst([areas[i]], side);
    let j = i + 1;
    while (j < n) {
      const next = worst([...row.map((k) => areas[k]), areas[j]], side);
      if (next > best) break;
      row.push(j);
      best = next;
      j++;
    }
    const sum = row.reduce((a, k) => a + areas[k], 0);
    if (w >= h) {
      const stripW = h > 0 ? sum / h : 0;
      let yy = y;
      for (const k of row) {
        const hh = stripW > 0 ? areas[k] / stripW : 0;
        out[k] = { x, y: yy, w: stripW, h: hh };
        yy += hh;
      }
      x += stripW;
      w -= stripW;
    } else {
      const stripH = w > 0 ? sum / w : 0;
      let xx = x;
      for (const k of row) {
        const ww = stripH > 0 ? areas[k] / stripH : 0;
        out[k] = { x: xx, y, w: ww, h: stripH };
        xx += ww;
      }
      y += stripH;
      h -= stripH;
    }
    i = j;
  }
  return out;
}
