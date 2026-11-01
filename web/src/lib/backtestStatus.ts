// 回测状态的显示（列表与标题区共用）
export const STATUS_PILL: Record<string, string> = { done: "ok", running: "info", queued: "hold", failed: "err" };
export const STATUS_TEXT: Record<string, string> = { done: "完成", running: "运行中", queued: "排队", failed: "失败" };
export const SLEEVE_TEXT: Record<string, string> = { momentum: "动量仓", swing: "波段仓", main: "单仓" };
export const SLEEVE_COLOR: Record<string, string> = { momentum: "#2962ff", swing: "#a855f7", main: "#22c55e" };
