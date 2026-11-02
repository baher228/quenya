import type {
  Board, Core, Route, UploadResult,
  Brief, Checkout, FetchJob, Item, Outfit, OutfitsResponse, TaskStats, TasteState, TasteVersion,
} from "./types";
import { APP_ONLY_DETAIL } from "./constants";

export class ApiError extends Error {
  status: number;
  requestIds: string[];
  constructor(message: string, status: number, requestIds: string[] = []) {
    super(message);
    this.status = status;
    this.requestIds = requestIds;
  }
}

function apiErrorFromDetail(detail: unknown, status: number): ApiError {
  let message = `Request failed (${status})`;
  let ids: string[] = [];
  if (typeof detail === "string") message = detail;
  else if (detail && typeof detail === "object") {
    const d = detail as { error?: string; request_ids?: string[] };
    if (d.error) message = d.error;
    if (Array.isArray(d.request_ids)) ids = d.request_ids;
  }
  return new ApiError(message, status, ids);
}

export type ProfileResult = {
  profile_id: string;
  brief: Brief;
  taste: { version: number; profile: TasteVersion["profile"] };
  version: number;
  request_id: string | null;
  fetch_job_id?: string | null;
  board: Board;
  route: Route | null;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 90_000);
  let res: Response;
  try {
    const token = window.localStorage.getItem("quenya.token");
    const headers = new Headers(init?.headers);
    if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers,
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
    if (
      res.status === 401 ||
      (res.status === 403 && detail === APP_ONLY_DETAIL)
    ) {
      window.localStorage.removeItem("quenya.token");
      window.dispatchEvent(new Event("quenya:unauthorized"));
    }
    throw apiErrorFromDetail(detail, res.status);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  req<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

type OutfitJobResponse = {
  status: "queued" | "running" | "done" | "error";
  result?: OutfitsResponse;
  error?: { status_code: number; detail: unknown };
};

async function pollOutfitJob(jobId: string): Promise<OutfitsResponse> {
  const deadline = Date.now() + 5 * 60_000;
  while (Date.now() < deadline) {
    const delay = Math.min(1_500, deadline - Date.now());
    await new Promise(resolve => window.setTimeout(resolve, delay));
    if (Date.now() >= deadline) break;
    const job = await req<OutfitJobResponse>(
      `/api/outfits/jobs/${encodeURIComponent(jobId)}`,
    );
    if (job.status === "done" && job.result) return job.result;
    if (job.status === "error" && job.error) {
      throw apiErrorFromDetail(job.error.detail, job.error.status_code);
    }
  }
  throw new ApiError("Outfits are still being made", 504);
}

export const api = {
  invite: (token: string) => post<{ token: string; user: User }>("/api/auth/invite", { token }),
  adminLogin: (name: string, password: string) =>
    post<{ token: string; user: Pick<User, "id" | "name" | "role"> }>(
      "/api/auth/admin-login",
      { name, password },
    ),
  me: () => req<User>("/api/me"),
  logout: () => post<{ ok: boolean }>("/api/auth/logout"),
  changeAdminPassword: (currentPassword: string, newPassword: string) =>
    post<{ ok: boolean }>("/api/admin/password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  health: () => req<{
    items_total: number;
    items_tagged: number;
    llm_provider: string;
    llm_ready: boolean;
    models: Record<string, string>;
  }>("/api/health"),
  createProfile: (brief: Brief, newBoard = false) =>
    post<ProfileResult>(
      "/api/profile", { brief, new_board: newBoard },
    ),
  updateBrief: (boardId: string, brief: Brief) =>
    req<ProfileResult>(`/api/boards/${encodeURIComponent(boardId)}/brief`, {
      method: "PUT",
      body: JSON.stringify({ brief }),
    }),
  fetchJob: (id: string) => req<FetchJob>(`/api/fetch/${id}`),
  deck: (n = 10, board_id?: string) =>
    req<Item[] | { items: Item[] }>(
      `/api/deck?n=${n}${board_id ? `&board_id=${encodeURIComponent(board_id)}` : ""}`,
    ).then(r => (Array.isArray(r) ? r : r.items)),
  swipe: (item_id: string, liked: boolean, board_id?: string) =>
    post<{ swipes_since_update: number; update_due: boolean }>("/api/swipe", { item_id, liked, board_id }),
  updateTaste: (board_id?: string) =>
    post<TasteVersion>("/api/taste/update", board_id ? { board_id } : undefined),
  taste: (board_id?: string) =>
    req<TasteState>(`/api/taste${board_id ? `?board_id=${encodeURIComponent(board_id)}` : ""}`),
  outfits: async (count: number, locked_ids: string[] = [], exclude_ids: string[] = [], board_id?: string) => {
    const job = await post<{ job_id: string; status: string }>(
      "/api/outfits/jobs",
      { count, locked_ids, exclude_ids, board_id },
    );
    return pollOutfitJob(job.job_id);
  },
  swap: (outfitId: string, item_id: string, exclude_ids: string[] = [], board_id?: string) =>
    post<Outfit | { outfit: Outfit }>(`/api/outfits/${outfitId}/swap`, { item_id, exclude_ids, board_id }).then(r => ("outfit" in r ? r.outfit : r)),
  verdict: (outfitId: string, verdict: "like" | "reject", reason?: string, board_id?: string) =>
    post<{ outfit: Outfit; taste?: TasteVersion | null }>(`/api/outfits/${outfitId}/verdict`, { verdict, reason, board_id }),
  correctTags: (itemId: string, note: string) =>
    post<{ tags: Item["tags"]; request_id: string | null; taste?: TasteVersion | null }>(`/api/items/${itemId}/correct-tags`, { note }),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<UploadResult>("/api/uploads", { method: "POST", body: fd });
  },
  assignUpload: (uploadId: string, board_id: string | null, label?: string) =>
    post<{ board: Board; taste?: TasteVersion | null }>(`/api/uploads/${uploadId}/assign`, { board_id, label }),
  boards: () => req<Board[]>("/api/boards"),
  activate: (id: string) => post<{ board: Board }>(`/api/boards/${id}/activate`),
  rename: (id: string, label: string) =>
    req<{ board: Board }>(`/api/boards/${id}`, { method: "PATCH", body: JSON.stringify({ label }) }),
  correctRoute: (routeId: string, board_id: string | null, label?: string) =>
    post<{ board: Board; route: Route; taste?: TasteVersion | null }>(`/api/route/${routeId}/correct`, { board_id, label }),
  core: () => req<Core>("/api/core"),
  search: (q: string, opts?: { slot?: string; limit?: number }) => {
    const params = new URLSearchParams({ q, limit: String(opts?.limit ?? 20) });
    if (opts?.slot) params.set("slot", opts.slot);
    return req<Item[] | { items: Item[] }>(`/api/search?${params}`).then(r =>
      Array.isArray(r) ? r : r.items,
    );
  },
  searchLive: (q: string) =>
    post<Item[] | { items: Item[]; job_id?: string }>("/api/search/live", { q }).then(r =>
      Array.isArray(r) ? r : r.items,
    ),
  checkout: (outfit_id: string, board_id?: string) => post<Checkout>("/api/checkout", { outfit_id, board_id }),
  reset: () => post<unknown>("/api/reset"),
  deleteAccount: () => post<{ ok: boolean }>("/api/account/delete"),
  stats: () => req<TaskStats[]>("/api/model/stats"),
  adminWaitlist: (status: string) =>
    req<AdminWaitlistEntry[]>(`/api/admin/waitlist?status=${encodeURIComponent(status)}`),
  approveWaitlist: (id: string) =>
    post<{ invite_url: string; user: Pick<User, "id" | "name" | "role"> }>(
      `/api/admin/waitlist/${encodeURIComponent(id)}/approve`,
    ),
  emailInvite: (waitlistId: string, inviteUrl: string) =>
    post<{ ok: boolean; sent_to: string }>(
      `/api/admin/waitlist/${encodeURIComponent(waitlistId)}/email-invite`,
      { invite_url: inviteUrl },
    ),
  rejectWaitlist: (id: string) =>
    post<{ ok: boolean }>(`/api/admin/waitlist/${encodeURIComponent(id)}/reject`),
  adminUsers: () => req<AdminUser[]>("/api/admin/users"),
  rotateInvite: (id: string) =>
    post<{ invite_url: string }>(`/api/admin/users/${encodeURIComponent(id)}/rotate-invite`),
  revokeUser: (id: string) =>
    post<{ ok: boolean }>(`/api/admin/users/${encodeURIComponent(id)}/revoke`),
};

export type User = { id: string; name: string; role: "tester" | "admin"; max_styles: number | null; style_count: number };
export type AdminWaitlistEntry = {
  id: string; email: string; name: string | null; handle: string | null;
  style_text: string | null; wants_testing: boolean | number; devices: string | null;
  source: string | null; created_at: string; status: "pending" | "approved" | "rejected";
  reviewed_at: string | null; user_id: string | null;
};
export type AdminUser = {
  id: string; name: string; role: "tester" | "admin"; created_at: string;
  revoked_at: string | null; styles: { id: string; label: string }[];
  swipe_count: number; last_seen_at: string | null;
};

export const img = (it: Item) => it.image ?? it.image_url ?? "";
export const gbp = (n: number) => "£" + (Number.isInteger(n) ? n.toFixed(0) : n.toFixed(2));
export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
