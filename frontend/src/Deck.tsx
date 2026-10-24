import { useCallback, useEffect, useRef, useState } from "react";
import { api, gbp, img } from "./api";
import { ErrorBox, Loading, Provenance, pretty } from "./ui";
import type { Item, TasteProfile } from "./types";

export function DeckScreen({ profile, version, onSignal, onDone, onOpenItem }: {
  profile: TasteProfile | null;
  version: number | null;
  onSignal: (updateDue: boolean) => void;
  onDone: () => void;
  onOpenItem: (it: Item) => void;
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [seen, setSeen] = useState(0);
  const [kick, setKick] = useState<{ dir: number; at: number } | null>(null);
  const [nudge, setNudge] = useState(() => !sessionStorage.getItem("q-nudged"));

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setItems(await api.deck(10)); } catch (e) { setError(e); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const decide = useCallback((liked: boolean) => {
    const top = items[0];
    if (!top) return;
    setKick(null);
    setItems(prev => prev.slice(1));
    setSeen(n => n + 1);
    api.swipe(top.id, liked).then(r => onSignal(r.update_due)).catch(setError);
  }, [items, onSignal]);

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

  const likes = profile ? Object.entries(profile.likes).sort((a, b) => b[1] - a[1]).slice(0, 5) : [];
  const avoids = profile ? Object.entries(profile.avoids).sort((a, b) => b[1] - a[1]).slice(0, 3) : [];

  return (
    <section className="screen swipe">
      <div className="deck-head">
        <div><p className="eyebrow">Calibrating{version ? ` · taste v${version}` : ""}</p><p className="h3">Keep or pass?</p></div>
        <span className="deck-count">{seen} swiped</span>
      </div>
      <div className="deck-bar"><i style={{ width: `${Math.min(100, (seen % 10) * 10)}%` }} /></div>

      <div className="deck">
        {loading && <Loading>Picking pieces for you</Loading>}
        {!loading && !!error && <ErrorBox error={error} onRetry={load} />}
        {!loading && !error && items.length === 0 && (
          <div className="deck-done">
            <p className="h3">Enough to go on.</p>
            <p className="muted">Your swipes are in. Build outfits from them, or keep calibrating.</p>
            <button className="btn primary" onClick={onDone}>Build outfits</button>
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

      <div className="learned-wrap">
        <span className="lbl">Learned so far {version ? <em>taste v{version}</em> : null}</span>
        <div className="learned">
          {likes.length + avoids.length === 0 && <span className="muted small">Every swipe is a label. Your taste updates every 8 swipes.</span>}
          {likes.map(([k]) => <span key={"l" + k} className="sig pos">+ {pretty(k)}</span>)}
          {avoids.map(([k]) => <span key={"a" + k} className="sig neg">− {pretty(k)}</span>)}
        </div>
      </div>
    </section>
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
        <div className="row3"><Provenance source={item.source} origin={item.origin} condition={t?.condition ?? item.condition} />{item.size ? ` · size ${item.size}` : ""}</div>
        {t && <div className="tags">{[t.category, ...t.aesthetics.slice(0, 2), t.era !== "unknown" ? t.era : null].filter(Boolean).map(x => <span key={x} className="tag">{x}</span>)}</div>}
      </div>
    </div>
  );
}
