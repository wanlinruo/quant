import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import type { ApiResult } from "../hooks/useApi";
import { ApiState } from "./ApiState";

const failed = (status: number, detail: unknown): ApiResult<number[]> => ({
  data: null, error: new ApiError(status, detail), loading: false, reload: () => {},
});

describe("ApiState", () => {
  it("shows the database hint only for the no-database 503", () => {
    render(<ApiState result={failed(503, "未配置数据库（QUANT_DATABASE_URL）")}>{() => null}</ApiState>);
    expect(screen.getByText(/未配置数据库/)).toBeInTheDocument();
  });
  it("other 503 (e.g. proxy upstream down) is a generic error", () => {
    render(<ApiState result={failed(503, "Service Unavailable")}>{() => null}</ApiState>);
    expect(screen.getByText(/请求失败/)).toBeInTheDocument();
    expect(screen.queryByText(/未配置数据库/)).toBeNull();
  });
});
