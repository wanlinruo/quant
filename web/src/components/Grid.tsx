import type { ReactNode } from "react";

/** 12 列网格的一排：横纵间距 12px，同排等高。 */
export function Grid({ children }: { children: ReactNode }) {
  return <div className="g12">{children}</div>;
}

type Span = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12;

export function Cell({ span, children }: { span: Span; children: ReactNode }) {
  return (
    <div className="cell" style={{ gridColumn: `span ${span}` }}>
      {children}
    </div>
  );
}
