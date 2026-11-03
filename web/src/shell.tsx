import { createContext, useContext } from "react";
import type { MarketOverview, Meta, Mode } from "./api/types";
import type { ApiResult } from "./hooks/useApi";

export type Shell = { meta: Meta; account: Mode; market?: ApiResult<MarketOverview> };

export const ShellContext = createContext<Shell | null>(null);

/** 页面读取元信息与当前查看的账户。 */
export function useShell(): Shell {
  const s = useContext(ShellContext);
  if (!s) throw new Error("useShell 必须在 App 内使用");
  return s;
}
