export type * from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(ApiError.describe(detail, status));
    this.status = status;
    this.detail = detail;
  }

  /** 接口错误的中文说明；422 的错误列表用“；”连接。 */
  static describe(detail: unknown, status: number): string {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (typeof d === "string" ? d : (d as { msg?: string }).msg ?? JSON.stringify(d)))
        .join("；");
    }
    return `请求失败（${status}）`;
  }

  /** 422 时逐条错误，便于表单逐行显示。 */
  get messages(): string[] {
    return Array.isArray(this.detail)
      ? this.detail.map((d) => (typeof d === "string" ? d : (d as { msg?: string }).msg ?? ""))
      : [this.message];
  }
}

type Params = Record<string, string | number | (string | number)[] | undefined>;

function url(path: string, params?: Params): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined) continue;
    for (const x of Array.isArray(v) ? v : [v]) q.append(k, String(x));
  }
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

async function handle<T>(res: Response): Promise<T> {
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, (body as { detail?: unknown } | null)?.detail ?? null);
  }
  return body as T;
}

export const api = {
  async get<T>(path: string, params?: Params): Promise<T> {
    return handle<T>(await fetch(url(path, params)));
  },
  async post<T>(path: string, body: unknown): Promise<T> {
    return handle<T>(
      await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  },
};
