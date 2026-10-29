import { useCallback, useEffect, useRef, useState } from "react";
import { api, gbp, img } from "./api";
import { get as getCache, set as setCache } from "./cache";
import { ErrorBox, Loading, Provenance, pretty, uniqueTasteEntries } from "./ui";
import type { Item, TasteProfile } from "./types";

const CACHE_MAX_AGE_MS = 10 * 60 * 1000;
const CACHE_MIN_ITEMS = 3;

function appendUnique(existing: Item[], incoming: Item[], excluded: Set<string>) {
  const ids = new Set([...existing.map(item => item.id), ...excluded]);
  return [...existing, ...incoming.filter(item => !ids.has(item.id) && !!ids.add(item.id))];
}

export function DeckScreen({ profile, version, board, userId, boardId, swipesSinceUpdate, updating, onChange, onSignal, onDone, onOpenItem }: {
  profile: TasteProfile | null;
  version: number | null;
  board: string;
  userId: string;
  boardId: string;
  swipesSinceUpdate: number;
  updating: boolean;
  onChange: () => void;
  onSignal: (updateDue: boolean, boardId: string, swipesSinceUpdate: number) => void;
  onDone: (boardId: string) => void;
  onOpenItem: (it: Item) => void;
}) {
  const cached = useRef(getCache<Item[]>(userId, boardId, "deck")).current;
  const cachedItems = Array.isArray(cached?.data) ? cached.data : [];
  const [items, setItems] = useState<Item[]>(cachedItems);
  const [loading, setLoading] = useState(!cached);
  const [hasLoaded, setHasLoaded] = useState(!!cached);
  const [error, setError] = useState<unknown>(null);
  const [seen, setSeen] = useState(0);
  const [kick, setKick] = useState<{ dir: number; at: number } | null>(null);
  const [nudge, setNudge] = useState(() => !sessionStorage.getItem("q-nudged"));
  const initialized = useRef(false);
  const decidedIds = useRef(new Set<string>());

  const load = useCallback(async () => {
    const showLoading = items.length === 0;
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const incoming = await api.deck(10, boardId);
      setItems(current => appendUnique(current, incoming, decidedIds.current));
      setHasLoaded(true);
    } catch (e) {
      if (showLoading) setError(e);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [boardId, items.length]);

  const refreshQuietly = useCallback(async () => {
    try {
      const incoming = await api.deck(10, boardId);
      setItems(current => appendUnique(current, incoming, decidedIds.current));
      setHasLoaded(true);
    } catch {
      return;
    }
  }, [boardId]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    if (!cached) {
      void load();
    } else if (
      cachedItems.length < CACHE_MIN_ITEMS
      || Date.now() - cached.savedAt > CACHE_MAX_AGE_MS
    ) {
      void refreshQuietly();
    }
  }, [cached, cachedItems.length, load, refreshQuietly]);

  useEffect(() => {
    if (hasLoaded) setCache(userId, boardId, "deck", items);
  }, [boardId, hasLoaded, items, userId]);

  const decide = useCallback((liked: boolean) => {
    const top = items[0];
    if (!top) return;
    setKick(null);
    const remaining = items.slice(1);
    decidedIds.current.add(top.id);
    setItems(remaining);
    setCache(userId, boardId, "deck", remaining);
    setSeen(n => n + 1);
    const requestBoardId = boardId;
    api.swipe(top.id, liked, requestBoardId)
      .then(r => onSignal(r.update_due, requestBoardId, r.swipes_since_update))
      .catch(setError);
  }, [boardId, items, onSignal, userId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        if (!items.length) return;
        endNudge();
        setKick({ dir: e.key === "ArrowRight" ? 1 : -1, at: Date.now() });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items.length]);

  function endNudge() {
    if (!nudge) return;
    sessionStorage.setItem("q-nudged", "1");
    setNudge(false);
  }

  const likes = profile ? uniqueTasteEntries(
    Object.entries(profile.likes).sort((a, b) => b[1] - a[1]),
    5,
  ) : [];
  const avoids = profile ? uniqueTasteEntries(
    Object.entries(profile.avoids)
      .map(([key, value]) => [key, -value] as [string, number])
      .sort((a, b) => a[1] - b[1]),
    3,
  ) : [];

  return (
    <section className="screen swipe">
      <div className="swipe-main">
      <div className="deck-head">
        <button className="deck-ctx" onClick={onChange}><span>{board}</span>{version ? <em>v{version}</em> : null}<i>Change</i></button>
        <span className="deck-count">
          <TasteDots filled={swipesSinceUpdate} updating={updating} />
          <span>{seen}</span>
        </span>
      </div>

      <div className="deck">
        {loading && <Loading>Picking pieces for you</Loading>}
        {!loading && !!error && <ErrorBox error={error} onRetry={load} />}
        {!loading && !error && items.length === 0 && (
          <div className="deck-done">
            <p className="h3">Enough to go on.</p>
            <p className="muted">Your swipes are in. Build outfits from them, or keep calibrating.</p>
            <button className="btn primary" onClick={() => onDone(boardId)}>Build outfits</button>
            <button className="btn ghost" onClick={load}>10 more cards</button>
          </div>
        )}
        {!loading && items.slice(0, 3).reverse().map((it, i, arr) => (
          <Card key={it.id} item={it} depth={arr.length - 1 - i} onDecide={decide} onOpen={() => onOpenItem(it)}
            kick={arr.length - 1 - i === 0 ? kick : null}
            nudge={nudge && arr.length - 1 - i === 0} onTouch={endNudge} />
        ))}
      </div>

      {items.length > 0 && !loading && (
        <div className="swipe-hint" aria-hidden="true">
          <span className="h-pass">← Pass</span><span className="h-keep">Keep →</span>
        </div>
      )}
      </div>

      <div className="learned-wrap">
        <span className="lbl">Learned so far {version ? <em>taste v{version}</em> : null}</span>
        <div className="learned">
          {likes.length + avoids.length === 0 && <span className="muted small">Every swipe is a label. Your taste updates every 10 swipes.</span>}
          {likes.map(([k]) => <span key={"l" + k} className="sig pos">+ {pretty(k)}</span>)}
          {avoids.map(([k]) => <span key={"a" + k} className="sig neg">− {pretty(k)}</span>)}
        </div>
      </div>
    </section>
  );
}

function TasteDots({ filled, updating }: { filled: number; updating: boolean }) {
  const progress = Math.max(0, Math.min(10, filled));
  return (
    <span
      className={`taste-dots${updating ? " updating" : ""}`}
      role="img"
      aria-label={`${progress} of 10 swipes until your next taste update`}
    >
      {Array.from({ length: 10 }, (_, index) => (
        <span key={index} aria-hidden="true" className={index < progress ? "filled" : ""} />
      ))}
    </span>
  );
}

function Card({ item, depth, onDecide, onOpen, kick, nudge, onTouch }: {
  item: Item; depth: number; onDecide: (liked: boolean) => void; onOpen: () => void;
  kick: { dir: number; at: number } | null; nudge: boolean; onTouch: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef({ x0: 0, dx: 0, on: false, moved: false });

  const lean = (el: HTMLElement, dx: number) => {
    const l = Math.max(0, Math.min(1, dx / 90)), p = Math.max(0, Math.min(1, -dx / 90));
    el.style.setProperty("--l", String(l)); el.style.setProperty("--p", String(p));
    const s = el.closest<HTMLElement>(".screen");
    s?.style.setProperty("--l", String(l)); s?.style.setProperty("--p", String(p));
  };

  useEffect(() => {
    const el = ref.current;
    if (!kick || !el) return;
    lean(el, kick.dir * 90);
    fling(kick.dir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kick]);

  const reset = () => {
    const el = ref.current; if (!el) return;
    el.style.transition = "transform .25s"; el.style.transform = "";
    lean(el, 0);
  };
  const fling = (dir: number) => {
    const el = ref.current; if (!el) return;
    el.style.transition = "transform .3s cubic-bezier(.2,.7,.2,1), opacity .3s";
    el.style.transform = `translateX(${dir * 140}%) rotate(${dir * 12}deg)`;
    el.style.opacity = "0";
    window.setTimeout(() => {
      const s = el.closest<HTMLElement>(".screen");
      s?.style.setProperty("--l", "0"); s?.style.setProperty("--p", "0");
      onDecide(dir > 0);
    }, 220);
  };

  const t = item.tags;
  return (
    <div
      ref={ref}
      className={"card" + (nudge ? " nudge" : "")}
      onAnimationEnd={nudge ? onTouch : undefined}
      data-depth={depth}
      onPointerDown={depth === 0 ? e => {
        onTouch();
        drag.current = { x0: e.clientX, dx: 0, on: true, moved: false };
        e.currentTarget.setPointerCapture(e.pointerId);
        e.currentTarget.style.transition = "none";
      } : undefined}
      onPointerMove={depth === 0 ? e => {
        const d = drag.current; if (!d.on) return;
        d.dx = e.clientX - d.x0; if (Math.abs(d.dx) > 6) d.moved = true;
        const el = e.currentTarget;
        el.style.transform = `translateX(${d.dx}px) rotate(${d.dx / 22}deg)`;
        lean(el, d.dx);
      } : undefined}
      onPointerUp={depth === 0 ? () => {
        const d = drag.current; if (!d.on) return; d.on = false;
        if (Math.abs(d.dx) > 90) fling(Math.sign(d.dx));
        else { reset(); if (!d.moved) onOpen(); }
      } : undefined}
    >
      <div className="ph">
        <img src={img(item)} alt={item.title} draggable={false} />
        <span className="stamp like">Keep</span><span className="stamp pass">Pass</span>
      </div>
      <div className="meta">
        <div className="row1"><span className="brand">{item.brand || "Unbranded"}</span><span className="price">{gbp(item.price_gbp)}</span></div>
        <div className="name">{item.title}</div>
        <div className="row3"><Provenance source={item.source} condition={t?.condition ?? item.condition} />{item.size ? ` · size ${item.size}` : ""}</div>
        {t && <div className="tags">{[t.category, ...t.aesthetics.slice(0, 2), t.era !== "unknown" ? t.era : null].filter(Boolean).map(x => <span key={x} className="tag">{x}</span>)}</div>}
      </div>
    </div>
  );
}
