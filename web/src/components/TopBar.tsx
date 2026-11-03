import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router";
import type { MarketOverview, Meta, Mode } from "../api/types";
import { formatFreshness } from "../lib/time";
import { AccountSwitch } from "./AccountSwitch";
import { MarketFreshness } from "./MarketFreshness";
import { MarketTicker } from "./MarketTicker";

const NAV = [
  { to: "/", label: "市场", end: true },
  { to: "/portfolio", label: "组合", end: false },
  { to: "/backtest", label: "回测", end: false },
  { to: "/system", label: "系统", end: false },
];
const BASE_TITLE = "QUANT";

type Props = { meta: Meta; account: Mode; onAccountChange: (m: Mode) => void; market?: MarketOverview | null };

export function TopBar({ meta, account, onAccountChange, market }: Props) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const live = account === "live";

  useEffect(() => {
    document.title = live ? `[实盘] ${BASE_TITLE}` : BASE_TITLE;
  }, [live]);

  return (
    <header className="topbar">
      {live && <div className="live-stripe" data-testid="live-stripe" />}
      <span className="logo">
        <i />
        QUANT
      </span>
      <nav>
        {NAV.map((n) => (
          <NavLink key={n.to} to={{ pathname: n.to, search: `?account=${account}` }} end={n.end}>
            {n.label}
          </NavLink>
        ))}
      </nav>
      <input
        type="search"
        placeholder="⌕ 搜索代码，如 AAPL"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          const sym = q.trim().toUpperCase();
          if (e.key === "Enter" && sym) {
            navigate({ pathname: `/stock/${encodeURIComponent(sym)}`, search: `?account=${account}` });
            setQ("");
          }
        }}
      />
      <MarketTicker text={market?.highlights ?? ""} />
      <div className="topbar-data">
        {market?.status === "ok" ? <MarketFreshness market={market} lastJobFinished={meta.data_as_of} /> : (
          <span className="fresh" title="最近一次成功后台任务的完成时间；行情信息暂不可用">
            {formatFreshness(meta.data_as_of)}
          </span>
        )}
      </div>
      <AccountSwitch meta={meta} value={account} onChange={onAccountChange} />
    </header>
  );
}
