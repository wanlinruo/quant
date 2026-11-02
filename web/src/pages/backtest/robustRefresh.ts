/** 稳健性检验未完成时，稳健性标签自动刷新的间隔（测试环境缩短）。 */
export const ROBUST_REFRESH_MS = import.meta.env.MODE === "test" ? 200 : 30_000;
