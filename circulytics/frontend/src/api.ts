export const API = (import.meta.env.VITE_API_URL as string) || "/api";

export async function api<T = any>(path: string, opts: { method?: string; org?: number; body?: unknown } = {}): Promise<T> {
  const r = await fetch(API + path, {
    method: opts.method ?? "GET",
    headers: { "Content-Type": "application/json", ...(opts.org ? { "X-Org-Id": String(opts.org) } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${r.status}: ${data.detail ?? JSON.stringify(data)}`);
  return data as T;
}
