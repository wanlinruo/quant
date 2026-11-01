const TEXT = {
  none: "暂无数据",
  pending: "数据接入中",
  nodb: "未配置数据库（QUANT_DATABASE_URL）",
} as const;

export function EmptyState({ kind = "none", note }: { kind?: keyof typeof TEXT; note?: string }) {
  return (
    <div className="empty">
      <div>{TEXT[kind]}</div>
      {note && <div className="sm" style={{ marginTop: 4 }}>{note}</div>}
    </div>
  );
}
