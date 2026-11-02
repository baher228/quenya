import { useState } from "react";
import { api, gbp } from "./api";
import { ItemImage } from "./ItemImage";
import { ErrorBox, Loading, Provenance } from "./ui";
import type { Brief, Item, Slot } from "./types";

const EXAMPLE_QUERIES = [
  "brown suede loafers",
  "cable knit jumper",
  "wax jacket",
  "wide-leg trousers",
];

const SLOT_CHIPS: [Slot | "", string][] = [
  ["", "Any piece"],
  ["outerwear", "Outerwear"],
  ["top", "Tops"],
  ["knitwear", "Knitwear"],
  ["bottom", "Trousers"],
  ["shoes", "Shoes"],
  ["bag", "Bags"],
];

export function FindScreen({
  brief,
  onOpenItem,
}: {
  brief?: Brief | null;
  onOpenItem: (it: Item) => void;
}) {
  const [q, setQ] = useState("");
  const [slot, setSlot] = useState<Slot | "">(
    brief?.looking_for === "pieces" && brief.pieces.length === 1 ? brief.pieces[0] : "",
  );
  const [items, setItems] = useState<Item[] | null>(null);
  const [mode, setMode] = useState<"saved" | "live" | null>(null);
  const [lastSearchLive, setLastSearchLive] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const go = async (live: boolean, query = q) => {
    if (!query.trim()) return;
    const searchQuery = query.trim();
    setQ(searchQuery);
    setLastSearchLive(live);
    setMode(live ? "live" : "saved");
    setError(null);
    setItems(null);
    try {
      setItems(
        live
          ? await api.searchLive(searchQuery)
          : await api.search(searchQuery, { slot: slot || undefined, limit: 20 }),
      );
    } catch (e) {
      setError(e);
    } finally {
      setMode(null);
    }
  };

  return (
    <section className="screen">
      <p className="eyebrow">Single pieces</p>
      <h1 className="h1">Looking for one thing?</h1>
      <form className="search" onSubmit={e => { e.preventDefault(); void go(false); }}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="e.g. brown suede loafers" aria-label="Search pieces" />
        <button className="btn primary" disabled={!q.trim() || !!mode}>Search</button>
      </form>
      <div className="chips find-ex" role="group" aria-label="Piece type">
        {SLOT_CHIPS.map(([value, label]) => (
          <button
            key={label}
            type="button"
            className={"chip" + (slot === value ? " sel" : "")}
            aria-pressed={slot === value}
            onClick={() => setSlot(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {brief?.budget_gbp ? (
        <p className="small muted">
          Ranked by your taste
          {brief.sizes?.shoes || brief.sizes?.tops || brief.sizes?.waist
            ? ` · sizes ${[brief.sizes.tops && `tops ${brief.sizes.tops}`, brief.sizes.waist && `waist ${brief.sizes.waist}`, brief.sizes.shoes && `UK ${brief.sizes.shoes}`].filter(Boolean).join(", ")}`
            : ""}
          {` · up to £${brief.budget_gbp}`}. Live search pulls fresh Vinted and shop listings in the background.
        </p>
      ) : (
        <p className="small muted">Ranked by your taste. Search live to fetch fresh listings.</p>
      )}
      {items === null && !mode && (
        <div className="chips find-ex" role="group" aria-label="Example searches">
          {EXAMPLE_QUERIES.map(query => (
            <button key={query} type="button" className="chip" onClick={() => void go(false, query)}>{query}</button>
          ))}
          <button
            type="button"
            className="chip"
            disabled={!q.trim()}
            onClick={() => void go(true)}
          >
            Search live
          </button>
        </div>
      )}
      {mode && <Loading>{mode === "live" ? "Fetching live listings" : "Searching"}</Loading>}
      {!!error && <ErrorBox error={error} onRetry={() => go(lastSearchLive)} />}
      {items && items.length === 0 && <p className="empty muted">Nothing in the pool matches. Try searching live.</p>}
      {items && (
        <div className="grid">
          {items.map(it => (
            <button key={it.id} className="tile" onClick={() => onOpenItem(it)}>
              <ItemImage item={it} alt={it.title} loading="lazy" />
              <span className="t-meta"><b>{gbp(it.price_gbp)}</b><span>{it.title}</span><Provenance source={it.source} /></span>
            </button>
          ))}
        </div>
      )}
      {(items !== null || error !== null) && (
        <button className="btn ghost wide" disabled={!q.trim() || !!mode} onClick={() => go(true)}>
          Search live for “{q}”
        </button>
      )}
    </section>
  );
}
