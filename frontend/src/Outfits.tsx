import { useState } from "react";
import { api, gbp, img } from "./api";
import { ErrorBox, Loading, Provenance } from "./ui";
import type { Item, Outfit } from "./types";

export const REASONS = ["Too generic", "Colours don't work together", "Too expensive", "Wrong fit for me", "Too costume-y"];

export function OutfitsScreen({ outfits, loading, error, version, onBuild, onReplace, onReject, onLike, onCheckout, onOpenItem }: {
  outfits: Outfit[];
  loading: boolean;
  error: unknown;
  version: number | null;
  onBuild: () => void;
  onReplace: (old: Outfit, next: Outfit) => void;
  onReject: (o: Outfit) => void;
  onLike: (o: Outfit) => void;
  onCheckout: (o: Outfit) => void;
  onOpenItem: (it: Item) => void;
}) {
  const [locked, setLocked] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [rowErr, setRowErr] = useState<Record<string, unknown>>({});
  const [seen, setSeen] = useState<Record<string, string[]>>({});

  const toggleLock = (o: Outfit, id: string) =>
    setLocked(l => ({ ...l, [o.id]: (l[o.id] ?? []).includes(id) ? l[o.id].filter(x => x !== id) : [...(l[o.id] ?? []), id] }));

  const run = async (o: Outfit, key: string, fn: () => Promise<Outfit>) => {
    setBusy(o.id + key); setRowErr(e => ({ ...e, [o.id]: null }));
    try {
      const next = await fn();
      setLocked(l => ({ ...l, [next.id]: l[o.id] ?? [] }));
      if (key.startsWith("swap")) {
        setSeen(v => ({ ...v, [next.id]: [...new Set([...(v[o.id] ?? []), ...o.pieces.map(p => p.id)])] }));
      }
      onReplace(o, next);
    } catch (e) { setRowErr(r => ({ ...r, [o.id]: e })); } finally { setBusy(null); }
  };

  const swap = (o: Outfit, id: string) =>
    run(o, "swap" + id, () => api.swap(o.id, id, (seen[o.id] ?? []).slice(-300)));

  const rebuild = (o: Outfit) => run(o, "rebuild", async () => {
    const keep = locked[o.id] ?? [];
    const others = outfits.filter(x => x.id !== o.id).flatMap(x => x.pieces.map(p => p.id));
    const exclude = [...o.pieces.map(p => p.id).filter(id => !keep.includes(id)), ...others];
    const r = await api.outfits(1, keep, exclude);
    if (!r.outfits.length) throw new Error("No outfit fits those locks and your budget. Unlock a piece and retry.");
    return r.outfits[0];
  });

  return (
    <section className="screen">
      <p className="eyebrow">{outfits.length ? `${outfits.length} outfits${version ? ` · taste v${version}` : ""}` : "Outfits"}</p>
      <h1 className="h1">Put together for you.</h1>
      {loading && <Loading>Styling outfits from your pool</Loading>}
      {!loading && !!error && <ErrorBox error={error} onRetry={onBuild} />}
      {!loading && !error && outfits.length === 0 && (
        <div className="empty"><p className="muted">No outfits yet. Swipe a few cards first, then build.</p>
          <button className="btn primary" onClick={onBuild}>Build outfits</button></div>
      )}
      <div className="outfits">
        {outfits.map(o => {
          const keep = locked[o.id] ?? [];
          return (
            <article key={o.id} className={"outfit" + (o.verdict === "reject" ? " rejected" : o.verdict === "like" ? " loved" : "")}>
              <header>
                <div><div className="o-name">{o.title}</div><div className="o-mood">{o.mood}</div></div>
                <div className="o-total">{gbp(o.total_gbp)}<span>{o.pieces.length} pieces</span></div>
              </header>
              {(() => {
                const major = o.pieces.filter(p => !isMinor(p));
                const minor = o.pieces.filter(isMinor);
                return <>
                  {!!major.length && <div className="pieces">
                    {major.map((p, index) => (
                      <figure key={p.id} className={"piece" + (keep.includes(p.id) ? " locked" : "") + (major.length % 2 === 1 && index === 0 ? " wide" : "")}>
                        <button className="piece-hit" onClick={() => onOpenItem(p)} aria-label={`Details for ${p.title}`}><img src={img(p)} alt={p.title} loading="lazy" /></button>
                        <figcaption><b>{p.brand || p.tags?.category || "Piece"}</b><span>{gbp(p.price_gbp)}</span></figcaption>
                        <button className="pbtn lock" onClick={() => toggleLock(o, p.id)} aria-pressed={keep.includes(p.id)}>{keep.includes(p.id) ? "Locked" : "Lock"}</button>
                        <button className="pbtn swap" disabled={!!busy} onClick={() => swap(o, p.id)}>
                          {busy === o.id + "swap" + p.id ? "…" : "Swap"}</button>
                      </figure>
                    ))}
                  </div>}
                  {!!minor.length && <div className="minor-list">
                    {minor.map(p => (
                      <div key={p.id} className={"minor-row" + (keep.includes(p.id) ? " locked" : "")}>
                        <button className="minor-hit" onClick={() => onOpenItem(p)} aria-label={`Details for ${p.title}`}>
                          <img src={img(p)} alt={p.title} loading="lazy" />
                        </button>
                        <button className="minor-copy" onClick={() => onOpenItem(p)}>
                          <span>{p.brand ? `${p.brand} · ` : ""}{p.title}</span>
                          <small>{gbp(p.price_gbp)}</small>
                        </button>
                        <div className="minor-actions">
                          <button onClick={() => toggleLock(o, p.id)} aria-pressed={keep.includes(p.id)}>{keep.includes(p.id) ? "Locked" : "Lock"}</button>
                          <button disabled={!!busy} onClick={() => swap(o, p.id)}>
                            {busy === o.id + "swap" + p.id ? "…" : "Swap"}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>}
                </>;
              })()}
              <p className="why"><span>Why this</span>{o.why}</p>
              <ul className="srcs">{o.pieces.map(p => (
                <li key={p.id}><a href={p.source_url} target="_blank" rel="noreferrer">{p.title}</a><Provenance source={p.source} /></li>
              ))}</ul>
              {!!rowErr[o.id] && <div className="pad"><ErrorBox error={rowErr[o.id]} /></div>}
              <footer>
                <button className="btn ghost" onClick={() => onReject(o)} disabled={!!busy}>Not for me</button>
                <button className="btn ghost" onClick={() => rebuild(o)} disabled={!!busy}>{busy === o.id + "rebuild" ? "Rebuilding…" : keep.length ? "Rebuild around locks" : "Rebuild"}</button>
                <button className="btn primary yes" onClick={() => onLike(o)} disabled={!!busy || o.verdict === "like"}>{o.verdict === "like" ? "Saved" : "Love it"}</button>
              </footer>
              <div className="buy">
                <button className="btn ghost wide" onClick={() => onCheckout(o)}>Buy the bundle · {gbp(o.total_gbp)}</button>
              </div>
              {o.verdict === "reject" && o.reason && (
                <div className="corr">Correction recorded: <b>{o.reason}</b>. Your taste was updated and the next outfits use it.</div>
              )}
            </article>
          );
        })}
      </div>
      {outfits.length > 0 && !loading && <button className="btn ghost wide" onClick={onBuild}>New set from taste v{version ?? "–"}</button>}
    </section>
  );
}

const MINOR_SLOTS = new Set(["shoes", "bag", "accessory"]);
function isMinor(p: Item) { return MINOR_SLOTS.has(p.tags?.slot ?? ""); }
