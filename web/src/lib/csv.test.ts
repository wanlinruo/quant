import { describe, expect, it, vi } from "vitest";
import { downloadCsv, toCsv } from "./csv";

describe("toCsv", () => {
  it("starts with a BOM so Excel reads Chinese as UTF-8", () => {
    expect(toCsv([["a", "b"], [1, "止损"]]).startsWith("﻿a,b\r\n1,止损")).toBe(true);
  });
  it("quotes fields with commas, quotes or newlines and blanks null", () => {
    expect(toCsv([['跌破支撑, 止损', 'say "hi"', null, undefined, "x\ny"]])).toBe('﻿"跌破支撑, 止损","say ""hi""",,,"x\ny"');
  });

  it("releases a download URL after the click has started", () => {
    vi.useFakeTimers();
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:backtest");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    downloadCsv("result.csv", [["date", "return"]]);

    expect(click).toHaveBeenCalledOnce();
    expect(create).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revoke).toHaveBeenCalledWith("blob:backtest");
    vi.useRealTimers();
  });
});
