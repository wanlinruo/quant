export const STATUS_TEXT: Record<string, string> = {
  success: "成功", retry_success: "重试后成功", failed: "失败", running: "运行中",
  missing: "未运行", pending: "未到时间", na: "未上线",
};

export const SOURCE_TEXT: Record<string, [string, string]> = {
  ok: ["正常", "ok"], stale: ["过期", "warn"], pending: ["待接入", "hold"], warn: ["注意", "warn"],
};

export function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}
