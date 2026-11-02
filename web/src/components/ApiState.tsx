import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { ApiResult } from "../hooks/useApi";
import { EmptyState } from "./EmptyState";

type Props<T> = {
  result: ApiResult<T>;
  children: (data: T) => ReactNode;
  /** 数据为空数组时显示“暂无数据” */
  emptyWhen?: (data: T) => boolean;
};

/** 统一处理加载中、未配置数据库（503）、其它错误和空数据。 */
export function ApiState<T>({ result, children, emptyWhen }: Props<T>) {
  const { data, error, loading } = result;
  if (error) {
    // 只有后端明确说“未配置数据库”才提示；其它 503（如 Caddy 上游不可用）按普通错误显示
    if (error instanceof ApiError && error.status === 503 && String(error.detail).startsWith("未配置数据库")) return <EmptyState kind="nodb" />;
    return <div className="error-box">请求失败：{error.message}</div>;
  }
  if (loading || data === null) return <div className="muted">加载中…</div>;
  if (emptyWhen ? emptyWhen(data) : Array.isArray(data) && data.length === 0) return <EmptyState />;
  return <>{children(data)}</>;
}
