import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

type Params = Record<string, string | number | (string | number)[] | undefined>;

export type ApiResult<T> = {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
};

/** GET 接口；path 或 params 变化时重新请求。path 为 null 时不请求。 */
export function useApi<T>(path: string | null, params?: Params): ApiResult<T> {
  const key = path === null ? null : `${path}?${JSON.stringify(params ?? {})}`;
  const [state, setState] = useState<{ key: string | null; data: T | null; error: ApiError | Error | null }>(
    { key: null, data: null, error: null },
  );
  const [tick, setTick] = useState(0);
  const args = useRef({ path, params });
  useEffect(() => {
    args.current = { path, params }; // 先于下方请求 effect 执行
  });

  useEffect(() => {
    const { path: p, params: q } = args.current;
    if (p === null) return;
    let alive = true;
    api
      .get<T>(p, q)
      .then((data) => alive && setState({ key, data, error: null }))
      .catch((e: unknown) => alive && setState({ key, data: null, error: e instanceof Error ? e : new Error(String(e)) }));
    return () => {
      alive = false;
    };
  }, [key, tick]); // key 已包含 path 与 params

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = state.key === key;
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: path !== null && !current,
    reload,
  };
}
