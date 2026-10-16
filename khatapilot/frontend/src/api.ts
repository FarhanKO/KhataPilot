// A thin client for the backend. Requests go to /api on the dev server, whose proxy adds the owner token.

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const json = await response.json();
      detail = typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail ?? json);
    } catch {
      /* not JSON */
    }
    if (response.status === 401) detail = "The app is not signed in to the backend (check OWNER_TOKEN in .env).";
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof TypeError) return "The backend is not reachable. Is it running on port 8000?";
  return error instanceof Error ? error.message : String(error);
}
