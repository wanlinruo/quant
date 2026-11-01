import { useCallback } from "react";
import { useSearchParams } from "react-router";
import type { Meta, Mode } from "../api/types";

const MODES: Mode[] = ["sim", "paper", "live"];

/** 当前查看的账户：网址参数 ?account=；非法或没有数据时回落到默认账户。 */
export function useAccount(meta: Meta): { account: Mode; setAccount: (m: Mode) => void } {
  const [params, setParams] = useSearchParams();
  const raw = params.get("account");
  const valid =
    raw !== null &&
    (MODES as string[]).includes(raw) &&
    meta.accounts.some((a) => a.mode === raw && a.has_data);
  const account: Mode = valid ? (raw as Mode) : meta.default_account;

  const setAccount = useCallback(
    (m: Mode) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("account", m);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  return { account, setAccount };
}
