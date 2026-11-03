import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderAt } from "../test/utils";
import { CalendarPanel } from "./CalendarPanel";

afterEach(() => { vi.useRealTimers(); });

describe("CalendarPanel dates", () => {
  it("does not call yesterday today when reading an older calendar", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T16:00:00Z"));
    mockApi({ "/api/market/calendar": [200, {
      status: "ok", generated_at: "2026-11-02T21:00:00-05:00",
      released: [{ time: "2026-11-02T10:00:00-05:00", kind: "econ", title: "PMI", actual: "54", forecast: null, previous: null }],
      upcoming: [],
    }] });
    renderAt("/", <CalendarPanel height={380} />);
    expect(await screen.findByText("11-02 周一 10:00")).toBeInTheDocument();
    expect(screen.queryByText("今日 10:00")).toBeNull();
    expect(screen.getByText(/日历更新于 2026-11-02 21:00 ET/)).toBeInTheDocument();
  });
});
