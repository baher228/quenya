import type {
  Brief, Checkout, FetchJob, Item, Outfit, OutfitsResponse, TaskStats, TasteState, TasteVersion,
} from "./types";

export class ApiError extends Error {
  status: number;
  requestIds: string[];
  constructor(message: string, status: number, requestIds: string[] = []) {
    super(message);
    this.status = status;
    this.requestIds = requestIds;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 90_000);
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "AbortError";
    throw new ApiError(timedOut ? "The request timed out." : "Can't reach the Quenya API.", 0);
  } finally {
    window.clearTimeout(timer);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail ?? body;
    let message = `Request failed (${res.status})`;
    let ids: string[] = [];
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object") {
      const d = detail as { error?: string; request_ids?: string[] };
      if (d.error) message = d.error;
      if (Array.isArray(d.request_ids)) ids = d.request_ids;
    }
    throw new ApiError(message, res.status, ids);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  req<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

export const api = {
  health: () => req<{ items_total: number; items_tagged: number; models: Record<string, string> }>("/api/health"),
  createProfile: (brief: Brief) =>
    post<{ profile_id: string; taste: { version: number; profile: TasteVersion["profile"] }; fetch_job_id?: string | null }>(
      "/api/profile", { brief },
    ),
  fetchJob: (id: string) => req<FetchJob>(`/api/fetch/${id}`),
  deck: (n = 10) => req<Item[] | { items: Item[] }>(`/api/deck?n=${n}`).then(r => (Array.isArray(r) ? r : r.items)),
  swipe: (item_id: string, liked: boolean) =>
    post<{ swipes_since_update: number; update_due: boolean }>("/api/swipe", { item_id, liked }),
  updateTaste: () => post<TasteVersion>("/api/taste/update"),
  taste: () => req<TasteState>("/api/taste"),
  outfits: (count: number, locked_ids: string[] = [], exclude_ids: string[] = []) =>
    post<OutfitsResponse>("/api/outfits", { count, locked_ids, exclude_ids }),
  swap: (outfitId: string, item_id: string) =>
    post<Outfit | { outfit: Outfit }>(`/api/outfits/${outfitId}/swap`, { item_id }).then(r => ("outfit" in r ? r.outfit : r)),
  verdict: (outfitId: string, verdict: "like" | "reject", reason?: string) =>
    post<{ outfit: Outfit; taste?: TasteVersion | null }>(`/api/outfits/${outfitId}/verdict`, { verdict, reason }),
  correctTags: (itemId: string, note: string) =>
    post<{ tags: Item["tags"]; request_id: string | null; taste?: TasteVersion | null }>(`/api/items/${itemId}/correct-tags`, { note }),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ read: { one_line: string; aesthetics: string[]; palette: string[] }; taste?: TasteVersion | null }>(
      "/api/uploads", { method: "POST", body: fd },
    );
  },
  search: (q: string) =>
    req<Item[] | { items: Item[] }>(`/api/search?q=${encodeURIComponent(q)}&limit=20`).then(r => (Array.isArray(r) ? r : r.items)),
  searchLive: (q: string) =>
    post<Item[] | { items: Item[] }>("/api/search/live", { q }).then(r => (Array.isArray(r) ? r : r.items)),
  checkout: (outfit_id: string) => post<Checkout>("/api/checkout", { outfit_id }),
  reset: () => post<unknown>("/api/reset"),
  stats: () => req<TaskStats[]>("/api/model/stats"),
};

export const img = (it: Item) => it.image ?? it.image_url ?? "";
export const gbp = (n: number) => "£" + (Number.isInteger(n) ? n.toFixed(0) : n.toFixed(2));
export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
