import type { Meta, Mode } from "../api/types";

type Props = { meta: Meta; value: Mode; onChange: (m: Mode) => void };

/** 账户切换：只改变查看哪个账户的数据，不改变服务器的交易模式。 */
export function AccountSwitch({ meta, value, onChange }: Props) {
  const shown = meta.accounts.filter((a) => a.mode !== "sim" || meta.trading_mode === "sim");
  return (
    <div className="acct" role="group" aria-label="查看账户">
      {shown.map((a) => (
        <button
          key={a.mode}
          type="button"
          className={a.mode}
          aria-pressed={a.mode === value}
          disabled={!a.has_data}
          title={a.has_data ? undefined : "暂无数据"}
          onClick={() => onChange(a.mode)}
        >
          {a.label}
        </button>
      ))}
    </div>
  );
}
