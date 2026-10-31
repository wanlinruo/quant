import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.title = "QUANT";
});

// jsdom 没有 canvas：图表组件在测试中替换为占位元素（图表在本地截图检查中验证）
vi.mock("../charts/LineChart", () => ({ LineChart: () => null }));
vi.mock("../charts/CandleChart", () => ({ CandleChart: () => null }));
