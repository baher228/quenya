import { useState } from "react";
import { api, gbp, img } from "./api";
import { ErrorBox, Loading, Provenance } from "./ui";
import type { Item } from "./types";

export function FindScreen({ onOpenItem }: { onOpenItem: (it: Item) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Item[] | null>(null);
  const [mode, setMode] = useState<"saved" | "live" | null>(null);
  const [error, setError] = useState<unknown>(null);

  const go = async (live: boolean) => {
    if (!q.trim()) return;
    setMode(live ? "live" : "saved"); setError(null); setItems(null);
    try { setItems(live ? await api.searchLive(q.trim()) : await api.search(q.trim())); }
    catch (e) { setError(e); } finally { setMode(null); }
  };

  return (
    <section className="screen">
      <p className="eyebrow">Single pieces</p>
      <h1 className="h1">Looking for one thing?</h1>
      <form className="search" onSubmit={e => { e.preventDefault(); void go(false); }}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="e.g. brown suede loafers" aria-label="Search pieces" />
        <button className="btn primary" disabled={!q.trim() || !!mode}>Search</button>
      </form>
      <p className="small muted">Ranked by your taste. Search Vinted live to fetch fresh listings, which get tagged as they arrive.</p>
      {mode && <Loading>{mode === "live" ? "Fetching and tagging live listings" : "Searching"}</Loading>}
      {!!error && <ErrorBox error={error} onRetry={() => go(mode === "live")} />}
      {items && items.length === 0 && <p className="empty muted">Nothing in the pool matches. Try searching Vinted live.</p>}
      {items && (
        <div className="grid">
          {items.map(it => (
            <button key={it.id} className="tile" onClick={() => onOpenItem(it)}>
              <img src={img(it)} alt={it.title} loading="lazy" />
              <span className="t-meta"><b>{gbp(it.price_gbp)}</b><span>{it.title}</span><Provenance source={it.source} /></span>
            </button>
          ))}
        </div>
      )}
      {(items !== null || error !== null) && <button className="btn ghost wide" disabled={!q.trim() || !!mode} onClick={() => go(true)}>Search Vinted live for “{q}”</button>}
    </section>
  );
}
