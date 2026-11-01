const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function parts(d: Date, timeZone: string) {
  const f = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  const wd = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday);
  const hour = p.hour === "24" ? "00" : p.hour;
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${hour}:${p.minute}`, week: WEEK[wd] };
}

/** “2026-10-30 周五 · 数据截至 20:15 ET（北京 08:15）”；业务时间按美东。 */
export function formatFreshness(iso: string | null | undefined): string {
  if (!iso) return "暂无数据";
  const d = new Date(iso);
  const et = parts(d, "America/New_York");
  const bj = parts(d, "Asia/Shanghai");
  return `${et.date} ${et.week} · 数据截至 ${et.time} ET（北京 ${bj.time}）`;
}

export function formatDateTimeET(iso: string | null | undefined): string {
  if (!iso) return "—";
  const et = parts(new Date(iso), "America/New_York");
  return `${et.date} ${et.time}`;
}
