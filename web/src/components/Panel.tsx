import type { ReactNode } from "react";

type Props = {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  /** 内容超出时在面板内滚动（同排等高时用） */
  scroll?: boolean;
  /** 账户相关面板在实盘时标题后加“ · 实盘” */
  live?: boolean;
  height?: number;
};

export function Panel({ title, right, children, scroll, live, height }: Props) {
  return (
    <section className={`panel${live ? " live-account" : ""}`} style={height ? { height } : undefined}>
      <div className="panel-h">
        <b>{title}</b>
        <span>{right}</span>
      </div>
      <div className={`panel-b${scroll ? " scroll" : ""}`}>{children}</div>
    </section>
  );
}
