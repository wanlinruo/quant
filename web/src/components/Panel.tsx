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
  /** 在纵向排列中占满剩余高度（让同排两列底边对齐） */
  grow?: boolean;
};

export function Panel({ title, right, children, scroll, live, height, grow }: Props) {
  return (
    <section className={`panel${live ? " live-account" : ""}`} style={{ ...(height ? { height } : {}), ...(grow ? { flex: "1 1 auto" } : {}) }}>
      <div className="panel-h">
        <b>{title}</b>
        <span>{right}</span>
      </div>
      <div className={`panel-b${scroll ? " scroll" : ""}`}>{children}</div>
    </section>
  );
}
