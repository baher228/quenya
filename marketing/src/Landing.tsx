import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import pieceData from "./pieces.json";
import { faq, shops, tasteRows } from "./content";
import { Page } from "./Shell";
import { WaitlistForm } from "./WaitlistForm";

type SetKey = "quiet" | "gorp" | "techno" | "graphic" | "people";
type Piece = {
  src: string;
  w: number;
  h: number;
  brand: string;
  title: string;
  price: number;
  shop: string;
  category: string;
  condition: string;
};
type PieceSet = { brief: string; items: Piece[] };

const pieceSets = pieceData as Record<SetKey, PieceSet>;
const styleKeys: SetKey[] = ["quiet", "gorp", "techno", "graphic"];
const allPieces = Object.values(pieceSets).flatMap(set => set.items);
const demoDeck = [...pieceSets.techno.items, ...pieceSets.graphic.items];
const people = pieceSets.people.items;

function formatPrice(price: number) {
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(price);
}

function displayBrand(brand: string) {
  return brand.toLowerCase() === "stussy" ? "Stüssy" : brand;
}

function displayTitle(title: string) {
  return title.replace(/^(Men's|Women's)\s+/i, "");
}

function productCaption(item: Piece) {
  return `${displayBrand(item.brand)} — ${displayTitle(item.title)} — ${formatPrice(item.price)} — ${item.shop}`;
}

function Ticker() {
  const line = "NEW + SECOND-HAND  ✕  ~70 UK SHOPS  ✕  LIVE VINTED  ✕  PRICES IN GBP  ✕  PRIVATE TASTE PER STYLE";
  return (
    <div className="ticker" aria-label={line}>
      <div className="ticker-track" aria-hidden="true">
        <span>{line}</span>
        <span>{line}</span>
      </div>
    </div>
  );
}

function Hero() {
  const [activeSet, setActiveSet] = useState<SetKey>("quiet");
  const [manualPick, setManualPick] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [visibleBrief, setVisibleBrief] = useState(pieceSets.quiet.brief);
  const brief = pieceSets[activeSet].brief;
  const activeItems = pieceSets[activeSet].items.slice(0, 4);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (reducedMotion) {
      setVisibleBrief(brief);
      return;
    }
    setVisibleBrief("");
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setVisibleBrief(brief.slice(0, index));
      if (index >= brief.length) window.clearInterval(timer);
    }, 38);
    return () => window.clearInterval(timer);
  }, [brief, reducedMotion]);

  useEffect(() => {
    if (manualPick || reducedMotion) return;
    const timer = window.setInterval(() => {
      setActiveSet(current => styleKeys[(styleKeys.indexOf(current) + 1) % styleKeys.length]);
    }, 4500);
    return () => window.clearInterval(timer);
  }, [manualPick, reducedMotion]);

  function chooseSet(key: SetKey) {
    setManualPick(true);
    setActiveSet(key);
  }

  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-copy">
        <p className="eyebrow">01 — Describe a style</p>
        <h1 id="hero-title" className="hero-brief">
          <span className="brief-quote">“</span>
          <span>{visibleBrief}</span>
          <span className="type-caret" aria-hidden="true" />
          <span className="brief-quote">”</span>
        </h1>
        <div className="style-chips" aria-label="Choose a style">
          {styleKeys.map(key => (
            <button
              key={key}
              type="button"
              aria-pressed={activeSet === key}
              onClick={() => chooseSet(key)}
            >
              {key}
            </button>
          ))}
        </div>
        <p className="hero-body">
          Quenya deals you real pieces from around 70 UK shops and live Vinted listings, learns from every keep and pass,
          and builds the whole outfit.
        </p>
        <div className="hero-action-row">
          <a className="button button-light" href="#waitlist">Request an invite</a>
          <span className="eyebrow">Invite-only · UK · Prices in GBP</span>
        </div>
      </div>
      <div className={`hero-stack hero-stack-${activeSet}`} role="group" aria-label={`Pieces for ${brief}`}>
        {activeItems.map((item, index) => (
          <figure key={`${activeSet}-${item.src}`} className={`hero-product-card hero-product-card-${index}`}>
            <img src={item.src} alt={`${displayBrand(item.brand)} ${displayTitle(item.title)}`} width={item.w} height={item.h} loading="eager" decoding="async" />
            <figcaption>{displayBrand(item.brand)} / {formatPrice(item.price)} / {item.shop}</figcaption>
          </figure>
        ))}
      </div>
      <div className="hero-wordmark" aria-hidden="true">QUENYA</div>
    </section>
  );
}

function SwipeDemo() {
  const [cardIndex, setCardIndex] = useState(0);
  const [dragX, setDragX] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [flying, setFlying] = useState<"keep" | "pass" | null>(null);
  const [kept, setKept] = useState(2);
  const [passed, setPassed] = useState(1);
  const pointerStart = useRef<{ x: number; pointerId: number } | null>(null);
  const item = demoDeck[cardIndex % demoDeck.length];

  function resolveSwipe(direction: "keep" | "pass") {
    if (flying) return;
    setFlying(direction);
    if (direction === "keep") setKept(value => value + 1);
    else setPassed(value => value + 1);
    window.setTimeout(() => {
      setCardIndex(value => value + 1);
      setDragX(0);
      setFlying(null);
    }, 280);
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (flying || event.button !== 0) return;
    pointerStart.current = { x: event.clientX, pointerId: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!pointerStart.current || pointerStart.current.pointerId !== event.pointerId) return;
    setDragX(event.clientX - pointerStart.current.x);
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>) {
    if (!pointerStart.current || pointerStart.current.pointerId !== event.pointerId) return;
    const distance = dragX;
    pointerStart.current = null;
    setDragging(false);
    if (distance > 120) resolveSwipe("keep");
    else if (distance < -120) resolveSwipe("pass");
    else setDragX(0);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      resolveSwipe("keep");
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      resolveSwipe("pass");
    }
  }

  const className = [
    "swipe-card",
    dragging ? "is-dragging" : "",
    flying ? `fly-${flying}` : "",
    dragX > 20 ? "show-keep" : "",
    dragX < -20 ? "show-pass" : "",
  ].filter(Boolean).join(" ");
  const transform = `translate3d(${dragX}px, 0, 0) rotate(${dragX / 22}deg)`;

  return (
    <div className="swipe-demo">
      <div className="swipe-card-stage">
        <div className="swipe-card-shadow" aria-hidden="true" />
        <div
          className={className}
          role="group"
          aria-label="Swipe demo card. Drag right to keep, left to pass. Use the arrow keys."
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
          style={{ transform }}
        >
          <span className="swipe-stamp stamp-keep" aria-hidden="true">KEEP</span>
          <span className="swipe-stamp stamp-pass" aria-hidden="true">PASS</span>
          <img src={item.src} alt={`${displayBrand(item.brand)} ${displayTitle(item.title)}`} width={item.w} height={item.h} loading="lazy" decoding="async" draggable={false} />
          <div className="swipe-card-caption">
            <span className="mono-label">{displayBrand(item.brand)} · {item.condition === "second-hand" ? "SECOND-HAND" : "NEW"}</span>
            <strong>{displayTitle(item.title)}</strong>
            <span className="mono-label">{formatPrice(item.price)} · {item.shop}</span>
          </div>
        </div>
      </div>
      <p className="swipe-count mono-label" aria-live="polite">KEPT {kept} · PASSED {passed}</p>
      <div className="swipe-actions">
        <button type="button" onClick={() => resolveSwipe("pass")}>← Pass</button>
        <button type="button" onClick={() => resolveSwipe("keep")}>Keep →</button>
      </div>
    </div>
  );
}

function TasteIndex() {
  const [visible, setVisible] = useState(false);
  const rowsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = rowsRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { threshold: 0.2 });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className={`taste-index ${visible ? "is-visible" : ""}`} ref={rowsRef}>
      {tasteRows.map((row, index) => {
        const positive = row.value > 0;
        const width = `${Math.abs(row.value) * 45}%`;
        return (
          <div className="taste-row" key={row.label}>
            <span className="taste-number">{String(index + 1).padStart(2, "0")}</span>
            <span className="taste-label">{row.label}</span>
            <span className="taste-track" aria-hidden="true">
              <span className={`taste-bar ${positive ? "bar-positive" : "bar-negative"}`} style={{ "--bar-width": width } as CSSProperties} />
            </span>
            <span className={`taste-value ${positive ? "value-positive" : "value-negative"}`}>{row.value > 0 ? "+" : "−"}{Math.abs(row.value).toFixed(1)}</span>
          </div>
        );
      })}
      <p className="taste-foot mono-label">TASTE V7 · 84 SWIPES</p>
    </div>
  );
}

function Lookbook() {
  const [look, setLook] = useState<"quiet" | "gorp">("quiet");
  const [slots, setSlots] = useState<number[]>([3, 4, 1, 5]);
  const [locked, setLocked] = useState<boolean[]>([false, false, false, false]);
  const [swapVersion, setSwapVersion] = useState<number[]>([0, 0, 0, 0]);
  const items = pieceSets[look].items;
  const selected = slots.map(index => items[index]);
  const total = selected.reduce((sum, item) => sum + item.price, 0);

  useEffect(() => {
    setSlots(look === "quiet" ? [3, 4, 1, 5] : [0, 2, 4, 5]);
    setLocked([false, false, false, false]);
    setSwapVersion([0, 0, 0, 0]);
  }, [look]);

  function swapSlot(slot: number) {
    setSlots(current => {
      const unused = items.findIndex((_, index) => !current.includes(index));
      const next = unused >= 0 ? unused : (current[slot] + 1) % items.length;
      return current.map((currentIndex, index) => index === slot ? next : currentIndex);
    });
    setSwapVersion(current => current.map((version, index) => index === slot ? version + 1 : version));
  }

  function toggleLock(slot: number) {
    setLocked(current => current.map((value, index) => index === slot ? !value : value));
  }

  return (
    <div className="lookbook">
      <div className="look-tabs" role="tablist" aria-label="Choose a look">
        <button type="button" role="tab" aria-selected={look === "quiet"} onClick={() => setLook("quiet")}>LOOK 01 QUIET</button>
        <button type="button" role="tab" aria-selected={look === "gorp"} onClick={() => setLook("gorp")}>LOOK 02 GORP</button>
      </div>
      <p className="look-name mono-label">LOOK 0{look === "quiet" ? "1" : "2"} — {look.toUpperCase()}</p>
      <div className="look-grid">
        {selected.map((item, slot) => (
          <figure className={`look-slot look-slot-${slot} ${locked[slot] ? "is-locked" : ""}`} key={`${look}-${slot}-${swapVersion[slot]}`}>
            <div className="look-image">
              <img src={item.src} alt={`${displayBrand(item.brand)} ${displayTitle(item.title)}`} width={item.w} height={item.h} loading="lazy" decoding="async" />
            </div>
            <figcaption className="look-caption">
              <span className="look-caption-text"><span>{String(slot + 1).padStart(2, "0")}</span> {productCaption(item)}</span>
              <span className="look-controls">
                <button type="button" aria-pressed={locked[slot]} aria-label={`${locked[slot] ? "Unlock" : "Lock"} ${displayBrand(item.brand)} ${displayTitle(item.title)}`} onClick={() => toggleLock(slot)}>{locked[slot] ? "LOCKED" : "LOCK"}</button>
                <button type="button" className="slot-swap" disabled={locked[slot]} aria-label={`Swap ${displayBrand(item.brand)} ${displayTitle(item.title)}`} onClick={() => swapSlot(slot)}>SWAP</button>
              </span>
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="look-total mono-label">
        <span>TOTAL {formatPrice(total)}</span>
        <a href="#waitlist">Buy the look →</a>
      </div>
    </div>
  );
}

function ShopsIndex() {
  const [hovered, setHovered] = useState<{ item: Piece; x: number; y: number } | null>(null);
  const rows = [
    ...shops.map((name, index) => ({ name, tag: "NEW", item: allPieces[index % allPieces.length] })),
    { name: "VINTED", tag: "SECOND-HAND", item: pieceSets.graphic.items[1] },
  ];

  function movePreview(event: MouseEvent<HTMLLIElement>, item: Piece) {
    setHovered({ item, x: event.clientX, y: event.clientY });
  }

  return (
    <div className="shops-index">
      <ol>
        {rows.map((row, index) => (
          <li key={row.name} onMouseEnter={event => movePreview(event, row.item)} onMouseMove={event => movePreview(event, row.item)} onMouseLeave={() => setHovered(null)}>
            <span className="shop-number">{String(index + 1).padStart(2, "0")}</span>
            <span className="shop-name">{row.name}</span>
            <span className="shop-type">{row.tag}</span>
          </li>
        ))}
      </ol>
      {hovered && (
        <div className="shop-preview" style={{ left: hovered.x + 20, top: hovered.y }}>
          <img src={hovered.item.src} alt="" width={hovered.item.w} height={hovered.item.h} loading="lazy" decoding="async" />
          <p className="mono-label">{displayBrand(hovered.item.brand)} / {formatPrice(hovered.item.price)}</p>
        </div>
      )}
    </div>
  );
}

function PeopleStrip() {
  return (
    <section className="people-section" aria-labelledby="people-title">
      <div className="section-heading">
        <h2 id="people-title" className="display-heading">Found on <i>Quenya</i></h2>
      </div>
      <div className="people-strip">
        {people.map((item, index) => (
          <figure className="people-card" key={item.src}>
            <img src={item.src} alt={`${displayBrand(item.brand)} ${displayTitle(item.title)}`} width={item.w} height={item.h} loading="lazy" decoding="async" />
            <figcaption><span>{String(index + 1).padStart(2, "0")}</span> {displayBrand(item.brand)} — {displayTitle(item.title)}</figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

export function Landing() {
  return (
    <Page home>
      <Hero />
      <Ticker />

      <section className="editorial-section method-section" id="method" aria-labelledby="method-title">
        <div className="section-copy">
          <p className="eyebrow">02 — Swipe</p>
          <h2 id="method-title" className="display-heading">SWIPE IT INTO <i>shape</i>.</h2>
          <p>One piece at a time. Keep or pass. Every swipe teaches that style what you mean.</p>
        </div>
        <SwipeDemo />
      </section>

      <section className="editorial-section learn-section" aria-labelledby="learn-title">
        <div className="section-copy">
          <p className="eyebrow">03 — Learn</p>
          <h2 id="learn-title" className="display-heading">TASTE, WRITTEN <i>down</i>.</h2>
          <p>Each style keeps its own private profile: the brands, colours and pieces it leans into, and the ones it now avoids. Nobody else sees it.</p>
        </div>
        <TasteIndex />
      </section>

      <section className="wear-section" aria-labelledby="wear-title">
        <div className="wear-intro">
          <p className="eyebrow">04 — Wear</p>
          <h2 id="wear-title" className="display-heading">THE WHOLE <i>look</i>.</h2>
          <p>Quenya builds full outfits from what you kept. Lock the pieces you love, swap the rest, and buy each one straight from the shop.</p>
        </div>
        <Lookbook />
      </section>

      <section className="index-section" id="index" aria-labelledby="index-title">
        <div className="index-heading">
          <p className="eyebrow">05 — Index</p>
          <h2 id="index-title" className="display-heading">WHERE IT<br />COMES FROM.</h2>
        </div>
        <ShopsIndex />
        <p className="index-note mono-label">Carhartt WIP via Goodhood and Oi Polloi. All prices in GBP.</p>
      </section>

      <PeopleStrip />

      <section className="faq-section" id="faq" aria-labelledby="faq-title">
        <div className="faq-heading">
          <p className="eyebrow">Questions</p>
          <h2 id="faq-title" className="display-heading">QUESTIONS.</h2>
        </div>
        <div className="faq-list">
          {faq.map((entry, index) => (
            <details key={entry.q}>
              <summary><span className="faq-number">{String(index + 1).padStart(2, "0")}</span>{entry.q}</summary>
              <p>{entry.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="waitlist-section" id="waitlist" aria-labelledby="waitlist-title">
        <div className="waitlist-copy">
          <p className="eyebrow">Tester waitlist</p>
          <h2 id="waitlist-title" className="display-heading">REQUEST AN <i>invite</i>.</h2>
          <p>Quenya is in invite-only testing. Leave your email and we'll let you know when there's a place. An iPhone app is in development, so tell us what you'd test on.</p>
        </div>
        <WaitlistForm />
      </section>
    </Page>
  );
}
