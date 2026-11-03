import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/utils";
import { useApi } from "./useApi";

afterEach(() => { vi.useRealTimers(); });

describe("useApi refresh", () => {
  it("polls, reloads on visibility, and cancels its timer on unmount", async () => {
    vi.useFakeTimers();
    const fetch = mockApi({ "/api/test": [200, { ok: true }] });
    const hook = renderHook(() => useApi("/api/test", undefined, 1000));
    await act(async () => {});
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fetch).toHaveBeenCalledTimes(3);
    hook.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
