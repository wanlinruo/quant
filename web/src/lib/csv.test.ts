import { describe, expect, it } from "vitest";
import { toCsv } from "./csv";

describe("toCsv", () => {
  it("starts with a BOM so Excel reads Chinese as UTF-8", () => {
    expect(toCsv([["a", "b"], [1, "止损"]]).startsWith("﻿a,b\r\n1,止损")).toBe(true);
  });
  it("quotes fields with commas, quotes or newlines and blanks null", () => {
    expect(toCsv([['跌破支撑, 止损', 'say "hi"', null, undefined, "x\ny"]])).toBe('﻿"跌破支撑, 止损","say ""hi""",,,"x\ny"');
  });
});
