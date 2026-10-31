import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { vi } from "vitest";
import type { Meta, Mode } from "../api/types";

export function mkMeta(over: Partial<Meta> & { has?: Partial<Record<Mode, boolean>> } = {}): Meta {
  const { has = {}, ...rest } = over;
  const labels: Record<Mode, string> = { sim: "本地模拟", paper: "模拟盘", live: "实盘" };
  return {
    trading_mode: "paper",
    broker: "tiger",
    database_configured: true,
    accounts: (["sim", "paper", "live"] as Mode[]).map((m) => ({
      mode: m,
      label: labels[m],
      has_data: has[m] ?? m === "paper",
    })),
    default_account: "paper",
    data_as_of: null,
    ...rest,
  };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

export function renderAt(path: string, ui: ReactNode) {
  const user = userEvent.setup();
  const r = render(
    <MemoryRouter initialEntries={[path]}>
      {ui}
      <LocationProbe />
    </MemoryRouter>,
  );
  return { ...r, user };
}

export function currentLocation(): string {
  return document.querySelector('[data-testid="location"]')?.textContent ?? "";
}

type Route = [number, unknown];

/** 按路径（不含查询参数）返回固定 JSON；未配置的路径返回 404。 */
export function mockApi(routes: Record<string, Route>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://x");
    const key = (init?.method ?? "GET") === "POST" ? `POST ${url.pathname}` : url.pathname;
    const [status, body] = routes[key] ?? [404, { detail: "Not Found" }];
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  });
}
