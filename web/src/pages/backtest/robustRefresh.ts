/** 稳健性检验未完成时，稳健性标签自动刷新的间隔（测试环境缩短）。 */
export const ROBUST_REFRESH_MS = import.meta.env.MODE === "test" ? 200 : 30_000;

/** 有排队或运行中的回测时，回测列表与分析自动刷新的间隔（测试环境缩短）。 */
export const RUNS_REFRESH_MS = import.meta.env.MODE === "test" ? 200 : 15_000;
