import { useEffect, useState } from "react";
import { BrowserRouter, MemoryRouter, Route, Routes } from "react-router";
import { api, ApiError } from "./api/client";
import type { Meta } from "./api/types";
import { TopBar } from "./components/TopBar";
import { useAccount } from "./hooks/useAccount";
import { BacktestPage } from "./pages/BacktestPage";
import { MarketPage } from "./pages/MarketPage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { StockPage } from "./pages/StockPage";
import { SystemPage } from "./pages/SystemPage";
import { ShellContext } from "./shell";

function Shell({ meta }: { meta: Meta }) {
  const { account, setAccount } = useAccount(meta);
  return (
    <ShellContext.Provider value={{ meta, account }}>
      <TopBar meta={meta} account={account} onAccountChange={setAccount} />
      <Routes>
        <Route path="/" element={<MarketPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/backtest/:id" element={<BacktestPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="/stock/:symbol" element={<StockPage />} />
        <Route path="*" element={<div className="page"><div className="empty">页面不存在</div></div>} />
      </Routes>
      <footer className="footer">
        图表由{" "}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          TradingView Lightweight Charts™
        </a>{" "}
        提供
      </footer>
    </ShellContext.Provider>
  );
}

/** initialPath 仅用于测试（内存路由）；正式运行用浏览器路由。 */
export function App({ initialPath }: { initialPath?: string }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => api
      .get<Meta>("/api/meta")
      .then((m) => { if (active) { setMeta(m); setError(null); } })
      .catch((e: unknown) => { if (active) setError(e instanceof ApiError ? e.message : String(e)); });
    void load();
    const timer = setInterval(() => { void load(); }, 3 * 60_000);
    return () => { active = false; clearInterval(timer); };
  }, []);

  if (error && !meta) {
    return (
      <div className="page">
        <div className="error-box">面板加载失败：{error}</div>
      </div>
    );
  }
  if (!meta) return <div className="page muted">加载中…</div>;
  const body = <Shell meta={meta} />;
  return initialPath ? (
    <MemoryRouter initialEntries={[initialPath]}>{body}</MemoryRouter>
  ) : (
    <BrowserRouter>{body}</BrowserRouter>
  );
}
