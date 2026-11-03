import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useETToday } from "./useETToday";

afterEach(() => { vi.useRealTimers(); });

describe("useETToday", () => {
  it("advances the label when an open page crosses New York midnight", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-03T04:59:30Z"));
    const hook = renderHook(() => useETToday());
    expect(hook.result.current).toBe("2026-11-02");
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(hook.result.current).toBe("2026-11-03");
    hook.unmount();
  });
});
