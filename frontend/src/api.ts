export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-Requested-With", "employee-assistant");
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("无法连接服务，请检查网络和后端是否启动。", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(
      typeof payload.detail === "string"
        ? payload.detail
        : "请求失败，请稍后重试。",
      response.status,
    );
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function displayTime(value: string): string {
  return new Date(value.replace(" ", "T") + "Z").toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
