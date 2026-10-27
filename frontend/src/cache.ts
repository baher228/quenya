const CACHE_PREFIX = "quenya.cache.";
const CACHE_VERSION_PREFIX = `${CACHE_PREFIX}v1:`;

export type CacheKind = "deck" | "outfits";

export interface CacheEntry<T> {
  savedAt: number;
  data: T;
}

export function cacheKey(userId: string, boardId: string, kind: CacheKind) {
  return `${CACHE_VERSION_PREFIX}${userId}:${boardId}:${kind}`;
}

export function get<T>(userId: string, boardId: string, kind: CacheKind): CacheEntry<T> | null {
  try {
    const raw = window.localStorage.getItem(cacheKey(userId, boardId, kind));
    if (!raw) return null;
    const entry = JSON.parse(raw) as Partial<CacheEntry<T>>;
    if (!Number.isFinite(entry.savedAt) || !("data" in entry)) return null;
    return { savedAt: entry.savedAt as number, data: entry.data as T };
  } catch {
    return null;
  }
}

export function set<T>(userId: string, boardId: string, kind: CacheKind, data: T) {
  try {
    window.localStorage.setItem(
      cacheKey(userId, boardId, kind),
      JSON.stringify({ savedAt: Date.now(), data }),
    );
    return true;
  } catch {
    return false;
  }
}

export function remove(userId: string, boardId: string, kind: CacheKind) {
  try {
    window.localStorage.removeItem(cacheKey(userId, boardId, kind));
  } catch {
    return;
  }
}

export function clearAll() {
  try {
    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith(CACHE_PREFIX)) keys.push(key);
    }
    for (const key of keys) window.localStorage.removeItem(key);
  } catch {
    return;
  }
}
