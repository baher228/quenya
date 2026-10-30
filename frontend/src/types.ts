export type Slot = "outerwear" | "top" | "knitwear" | "bottom" | "dress" | "shoes" | "bag" | "accessory";

export interface Brief {
  text: string;
  looking_for: "outfits" | "pieces";
  pieces: Slot[];
  include_shoes: boolean;
  gender: "menswear" | "womenswear" | "both";
  condition: "second-hand" | "new" | "both";
  niche: number;
  budget_gbp: number;
  sizes: { tops: string; waist: string; shoes: string };
}

export interface ItemTags {
  slot: Slot;
  category: string;
  colours: string[];
  materials: string[];
  fit: string;
  era: string;
  aesthetics: string[];
  formality: number;
  niche: number;
  gender: string;
  logo_heavy: boolean;
  condition: string;
  one_line: string;
}

export interface Item {
  id: string;
  title: string;
  brand: string | null;
  price_gbp: number;
  size: string | null;
  source: string;
  source_url: string;
  condition?: string | null;
  image?: string;
  image_url?: string;
  image_preview?: string | null;
  tags: ItemTags | null;
  fetched_at?: string;
  origin?: "snapshot" | "live";
}

export interface TasteProfile {
  summary: string;
  likes: Record<string, number>;
  avoids: Record<string, number>;
  rules: string[];
  palette: string[];
  niche_level: number;
  price_ceiling_gbp: number;
  condition_preference: string;
  search_queries: string[];
  changes: string[];
}

export interface TasteVersion {
  version: number;
  profile: TasteProfile;
  changes?: string[];
  request_id?: string | null;
  latency_ms?: number;
}

export interface TasteState {
  current: { version: number; profile: TasteProfile } | null;
  history: {
    version: number;
    changes?: string[];
    profile?: TasteProfile & { changes?: string[] };
    created_at: string;
    request_id: string | null;
    signal_counts?: Record<string, number>;
  }[];
  counts: Record<string, number>;
  core?: Core;
}

export interface Outfit {
  id: string;
  title: string;
  mood: string;
  why: string;
  pieces: Item[];
  total_gbp: number;
  verdict?: "like" | "reject" | null;
  reason?: string | null;
  request_id?: string | null;
  taste_version?: number;
}

export interface OutfitsResponse {
  outfits: Outfit[];
  dropped?: number;
  request_ids?: string[];
}

export interface TaskStats {
  task: string;
  calls: number;
  ok: number;
  failed: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  total_tokens: number | null;
  last_request_ids: string[];
  calls_toward_training?: number;
  first_training_at?: number;
}

export interface Checkout {
  order_ref: string;
  simulated: boolean;
  lines: { title: string; source: string; source_url: string; price_gbp: number }[];
  total_gbp: number;
}

export interface FetchJob {
  status: "running" | "done" | "failed";
  fetched: number;
  tagged: number;
  failed: number;
  errors: string[];
}

export interface Board {
  id: string;
  label: string;
  context: string;
  active: boolean;
  brief: Brief;
  version: number | null;
  counts: { swipes: number; outfits: number; uploads: number };
}

export interface Route {
  id: string;
  routed: "existing" | "new" | "pending" | "corrected";
  context: string;
  label: string;
  confidence: number;
  reason: string;
  request_id: string | null;
}

export interface Core {
  rules: { rule: string; boards: string[] }[];
  likes: Record<string, number>;
  avoids: Record<string, number>;
  palette: string[];
  sizes: Record<string, string>;
}

export interface UploadResult {
  upload_id: string;
  read: { one_line: string; aesthetics: string[]; palette: string[] };
  pending: boolean;
  route: Route;
  taste?: TasteVersion | null;
}
