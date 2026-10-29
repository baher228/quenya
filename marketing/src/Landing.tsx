import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import pieceData from "./pieces.json";
import { faq, styleOptions, tasteRows } from "./content";
import { Page } from "./Shell";
import { WaitlistForm } from "./WaitlistForm";

type SetKey = "quiet" | "gorp" | "techno" | "graphic" | "people";
type StyleKey = Exclude<SetKey, "people">;
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
type SwipeDirection = "keep" | "pass";

const pieceSets = pieceData as Record<SetKey, PieceSet>;
const allPieces = Object.values(pieceSets).flatMap(set => set.items);
const people = pieceSets.people.items;

function srcSetFor(src: string) {
  return `${src.replace(/\.webp$/, "-600.webp")} 600w, ${src} 1200w`;
}
const shopPieces = [...new Map(allPieces.map(item => [item.shop, item])).values()];

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

function displayShopName(shop: string) {
  return shop
    .replace(/^https?:\/\/(?:www\.)?/i, "")
    .replace(/\/.*$/, "")
    .replace(/\.(co\.uk|com|co|uk|net|org)$/i, "")
    .split(/[._-]+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function useReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return reducedMotion;
}

function learningLabel(item: Piece, direction: SwipeDirection) {
  const category = item.category.toLowerCase();
  if (direction === "keep" && /(jacket|parka|shell|windbreaker)/.test(category)) return "technical shells";
  if (direction === "pass" && /(graphic|printed|logo)/.test(category)) return "logo prints";
  return category;
}

function Hero() {
  const reducedMotion = useReducedMotion();
  const [activeSet, setActiveSet] = useState<StyleKey>("gorp");
  const [manualPick, setManualPick] = useState(false);
  const [visibleBrief, setVisibleBrief] = useState("gorpcore");
  const [dealIn, setDealIn] = useState(true);
  const [cardIndex, setCardIndex] = useState(0);
  const [dragDistance, setDragDistance] = useState(0);
  const [dragSide, setDragSide] = useState<SwipeDirection | null>(null);
  const [dragging, setDragging] = useState(false);
  const [springing, setSpringing] = useState(false);
  const [flying, setFlying] = useState<SwipeDirection | null>(null);
  const [learned, setLearned] = useState<{ id: number; direction: SwipeDirection; label: string }[]>([]);
  const [kept, setKept] = useState(0);
  const [passed, setPassed] = useState(0);
  const pointer = useRef<{
    pointerId: number;
    startX: number;
    lastX: number;
    lastAt: number;
    velocity: number;
  } | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const flyingRef = useRef(false);
  const learnedIdRef = useRef(0);
  const brief = styleOptions.find(option => option.key === activeSet)?.brief ?? "gorpcore";
  const deckItems = pieceSets[activeSet].items;
  const current = deckItems[cardIndex % deckItems.length];

  useEffect(() => {
    if (reducedMotion) {
      setVisibleBrief(brief);
      setDealIn(false);
      return;
    }

    setVisibleBrief("");
    let length = 0;
    const timer = window.setInterval(() => {
      length += 1;
      setVisibleBrief(brief.slice(0, length));
      if (length >= brief.length) window.clearInterval(timer);
    }, 36);
    const dealTimer = window.setTimeout(() => setDealIn(false), 680);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(dealTimer);
    };
  }, [brief, reducedMotion]);

  useEffect(() => {
    if (manualPick || reducedMotion) return;
    const timer = window.setInterval(() => {
      setActiveSet(value => {
        const keys = styleOptions.map(option => option.key);
        return keys[(keys.indexOf(value) + 1) % keys.length] ?? keys[0];
      });
      setCardIndex(0);
      setLearned([]);
      setKept(0);
      setPassed(0);
    }, 5400);
    return () => window.clearInterval(timer);
  }, [manualPick, reducedMotion]);

  useEffect(() => () => {
    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
  }, []);

  function setCardTransform(card: HTMLDivElement, x: number) {
    const angle = Math.max(-17, Math.min(17, x / 17));
    card.style.transform = `translate3d(calc(-50% + ${x}px), -50%, 0) rotate(${angle}deg)`;
  }

  function springBack(fromX: number, releaseVelocity: number) {
    const card = cardRef.current;
    if (!card) return;
    setDragDistance(0);
    setDragSide(null);
    if (reducedMotion) {
      card.style.transform = "";
      setSpringing(false);
      return;
    }

    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    setSpringing(true);
    let position = fromX;
    let velocity = Math.max(-1700, Math.min(1700, releaseVelocity * 1000));
    let previousTime = performance.now();
    const tick = (time: number) => {
      const delta = Math.min((time - previousTime) / 1000, 0.032);
      previousTime = time;
      const acceleration = -400 * position - 30 * velocity;
      velocity += acceleration * delta;
      position += velocity * delta;
      setCardTransform(card, position);
      if (Math.abs(position) < 0.25 && Math.abs(velocity) < 2) {
        card.style.transform = "";
        frameRef.current = null;
        setSpringing(false);
        return;
      }
      frameRef.current = window.requestAnimationFrame(tick);
    };
    frameRef.current = window.requestAnimationFrame(tick);
  }

  function resolveSwipe(direction: SwipeDirection, card = cardRef.current, releaseVelocity = 0) {
    if (flyingRef.current || !current) return;
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    setManualPick(true);
    setDealIn(false);
    pointer.current = null;
    setDragging(false);
    setDragDistance(0);
    setDragSide(null);
    setLearned(values => [
      { id: learnedIdRef.current++, direction, label: learningLabel(current, direction) },
      ...values,
    ].slice(0, 5));
    if (direction === "keep") setKept(value => value + 1);
    else setPassed(value => value + 1);

    if (reducedMotion || !card) {
      setCardIndex(value => value + 1);
      if (card) card.style.transform = "";
      return;
    }

    flyingRef.current = true;
    setSpringing(false);
    setFlying(direction);
    const speed = Math.min(Math.abs(releaseVelocity), 1.8);
    const duration = Math.max(220, 370 - speed * 82);
    card.style.transition = `transform ${duration}ms cubic-bezier(.23,1,.32,1)`;
    window.requestAnimationFrame(() => {
      const distance = (Math.max(window.innerWidth * 1.2, 560) + speed * 180) * (direction === "keep" ? 1 : -1);
      const angle = (direction === "keep" ? 1 : -1) * (14 + speed * 4);
      card.style.transform = `translate3d(calc(-50% + ${distance}px), -50%, 0) rotate(${angle}deg)`;
    });
    timerRef.current = window.setTimeout(() => {
      card.style.transition = "";
      card.style.transform = "";
      setCardIndex(value => value + 1);
      setFlying(null);
      flyingRef.current = false;
      timerRef.current = null;
    }, duration + 20);
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (flyingRef.current || event.button !== 0) return;
    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    setSpringing(false);
    setManualPick(true);
    setDealIn(false);
    setDragDistance(0);
    pointer.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      lastX: event.clientX,
      lastAt: event.timeStamp,
      velocity: 0,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const start = pointer.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const distance = event.clientX - start.startX;
    const elapsed = Math.max(1, event.timeStamp - start.lastAt);
    start.velocity = (event.clientX - start.lastX) / elapsed;
    start.lastX = event.clientX;
    start.lastAt = event.timeStamp;
    setDragDistance(distance);
    setDragSide(distance > 20 ? "keep" : distance < -20 ? "pass" : null);
    setCardTransform(event.currentTarget, distance);
  }

  function onPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const start = pointer.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const distance = event.clientX - start.startX;
    const elapsed = Math.max(1, event.timeStamp - start.lastAt);
    const finalVelocity = (event.clientX - start.lastX) / elapsed;
    const velocity = Math.abs(finalVelocity) > 0.05 ? finalVelocity : start.velocity;
    pointer.current = null;
    setDragging(false);
    if (Math.abs(distance) > 104 || Math.abs(velocity) > 0.62) {
      resolveSwipe(distance >= 0 ? "keep" : "pass", event.currentTarget, velocity);
    } else {
      springBack(distance, velocity);
    }
  }

  function onPointerCancel(event: ReactPointerEvent<HTMLDivElement>) {
    const start = pointer.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const distance = event.clientX - start.startX;
    pointer.current = null;
    setDragging(false);
    springBack(distance, 0);
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

  function chooseStyle(key: StyleKey) {
    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    frameRef.current = null;
    timerRef.current = null;
    flyingRef.current = false;
    pointer.current = null;
    setManualPick(true);
    setDealIn(false);
    setActiveSet(key);
    setCardIndex(0);
    setDragging(false);
    setSpringing(false);
    setFlying(null);
    setLearned([]);
    setKept(0);
    setPassed(0);
    setDragDistance(0);
    setDragSide(null);
    if (cardRef.current) {
      cardRef.current.style.transition = "";
      cardRef.current.style.transform = "";
    }
  }

  const cardStack = [2, 1, 0].map(layer => ({
    layer,
    item: deckItems[(cardIndex + layer) % deckItems.length],
  }));

  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-inner page-width">
        <div className="hero-copy">
          <h1 id="hero-title">Describe a look. Swipe until it&apos;s yours.</h1>
          <p className="hero-lede">
            Real pieces from around 70 UK shops and live Vinted listings. Keep what feels right; Quenya learns the rest.
          </p>
          <div className="brief-control">
            <label className="brief-label" htmlFor="hero-brief">Try a style brief</label>
            <input
              id="hero-brief"
              className="brief-input"
              type="text"
              value={visibleBrief}
              placeholder={brief}
              readOnly
              aria-live="polite"
            />
          </div>
          <div className="style-options" role="group" aria-label="Choose a style">
            {styleOptions.map(option => (
              <button
                key={option.key}
                className="style-chip"
                type="button"
                aria-pressed={activeSet === option.key}
                onClick={() => chooseStyle(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <a className="button button-primary hero-cta" href="#waitlist">Join the waitlist</a>
        </div>

        <div className="hero-demo">
          <div className={`deck-stage${dealIn ? " is-dealing" : ""}`} role="group" aria-label={`Pieces for ${brief}`}>
            {cardStack.map(({ layer, item }) => {
              const isTop = layer === 0;
              const classes = [
                "deck-card",
                `deck-card--${isTop ? "top" : layer === 1 ? "middle" : "back"}`,
                isTop && dragging ? "is-dragging" : "",
                isTop && springing ? "is-springing" : "",
                isTop && flying ? `is-flying is-flying-${flying}` : "",
                isTop && dragSide === "keep" ? "show-keep" : "",
                isTop && dragSide === "pass" ? "show-pass" : "",
              ].filter(Boolean).join(" ");
              return (
                <article
                  key={`${activeSet}-${layer}-${item.src}`}
                  ref={isTop ? cardRef : undefined}
                  className={classes}
                  role={isTop ? "group" : undefined}
                  aria-label={isTop ? `${displayBrand(item.brand)}, ${displayTitle(item.title)}. Drag right to keep or left to pass.` : undefined}
                  aria-hidden={!isTop}
                  tabIndex={isTop ? 0 : undefined}
                  onPointerDown={isTop ? onPointerDown : undefined}
                  onPointerMove={isTop ? onPointerMove : undefined}
                  onPointerUp={isTop ? onPointerUp : undefined}
                  onPointerCancel={isTop ? onPointerCancel : undefined}
                  onKeyDown={isTop ? onKeyDown : undefined}
                >
                  {isTop && (
                    <>
                      <span
                        className="deck-stamp stamp-keep"
                        style={{
                          opacity: dragSide === "keep" ? Math.min(1, Math.max(0, (dragDistance - 12) / 80)) : 0,
                          transform: `translateY(${6 * (1 - Math.min(1, dragDistance / 92))}px) scale(${0.96 + 0.04 * Math.min(1, dragDistance / 92)}) rotate(${-8 + 8 * Math.min(1, dragDistance / 92)}deg)`,
                        }}
                        aria-hidden="true"
                      >Keep</span>
                      <span
                        className="deck-stamp stamp-pass"
                        style={{
                          opacity: dragSide === "pass" ? Math.min(1, Math.max(0, (-dragDistance - 12) / 80)) : 0,
                          transform: `translateY(${6 * (1 - Math.min(1, -dragDistance / 92))}px) scale(${0.96 + 0.04 * Math.min(1, -dragDistance / 92)}) rotate(${8 - 8 * Math.min(1, -dragDistance / 92)}deg)`,
                        }}
                        aria-hidden="true"
                      >Pass</span>
                    </>
                  )}
                  <img
                    className="deck-photo"
                    src={item.src}
                    srcSet={srcSetFor(item.src)}
                    sizes="(max-width: 720px) 80vw, 400px"
                    alt={isTop ? `${displayBrand(item.brand)} ${displayTitle(item.title)}` : ""}
                    width={item.w}
                    height={item.h}
                    loading="eager"
                    decoding="async"
                    draggable={false}
                  />
                  <div className="deck-caption">
                    <span className="deck-brand">{displayBrand(item.brand)}</span>
                    <strong>{displayTitle(item.title)}</strong>
                    <span className="deck-price">{formatPrice(item.price)} <span>{displayShopName(item.shop)}</span></span>
                  </div>
                </article>
              );
            })}
          </div>

          <div className="deck-learning" aria-live="polite">
            <div className="learning-topline">
              <span>Learned so far</span>
              <span className="learning-count">{kept} kept, {passed} passed</span>
            </div>
            <div className="learning-chips">
              {learned.length === 0 ? (
                <span className="learning-empty">Keep or pass a piece to shape this style.</span>
              ) : learned.map(entry => (
                <span className={`learning-chip ${entry.direction === "keep" ? "is-keep" : "is-pass"}`} key={entry.id}>
                  <span aria-hidden="true">{entry.direction === "keep" ? "+" : "−"}</span> {entry.label}
                </span>
              ))}
            </div>
          </div>
          <div className="deck-actions" role="group" aria-label="Choose what to do with this piece">
            <button className="deck-action action-pass" type="button" onClick={() => resolveSwipe("pass")}>Pass</button>
            <button className="deck-action action-keep" type="button" onClick={() => resolveSwipe("keep")}>Keep</button>
          </div>
        </div>
      </div>
    </section>
  );
}

function TasteColumn({ direction }: { direction: SwipeDirection }) {
  const likes = direction === "keep";
  const rows = tasteRows.filter(row => likes ? row.value > 0 : row.value < 0);
  return (
    <div className={`taste-column ${likes ? "taste-likes" : "taste-avoids"}`}>
      <div className="taste-intro">
        <span className="taste-mark" aria-hidden="true">{likes ? "+" : "−"}</span>
        <div>
          <h3>{likes ? "More like this" : "Less like this"}</h3>
          <p>{likes ? "What you keep gives each style its shape." : "Passing helps leave the wrong pieces behind."}</p>
        </div>
      </div>
      <div className="taste-chips" aria-label={likes ? "Things this style likes" : "Things this style avoids"}>
        {rows.slice(0, 3).map(row => <span className="taste-chip" key={row.label}>{row.label}</span>)}
      </div>
      <div className="taste-signals">
        {rows.map(row => (
          <div className="taste-signal" key={row.label}>
            <span>{row.label}</span>
            <span className="taste-track" aria-hidden="true">
              <span
                className="taste-bar"
                style={{ "--bar-width": `${Math.abs(row.value) * 100}%` } as CSSProperties}
              />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LearningSection() {
  return (
    <section className="learning-section section-space" id="method" aria-labelledby="learning-title">
      <div className="section-heading page-width">
        <div>
          <h2 id="learning-title">It learns from both directions.</h2>
          <p>Every style gets its own private taste profile, shaped by what you keep and what you pass.</p>
        </div>
      </div>
      <div className="taste-panel page-width">
        <TasteColumn direction="keep" />
        <TasteColumn direction="pass" />
      </div>
    </section>
  );
}

function OutfitPanel() {
  const reducedMotion = useReducedMotion();
  const [look, setLook] = useState<"quiet" | "gorp">("gorp");
  const [slots, setSlots] = useState<number[]>([0, 2, 4, 5]);
  const [locked, setLocked] = useState<boolean[]>([false, false, false, false]);
  const [swapping, setSwapping] = useState<{ slot: number; previous: Piece } | null>(null);
  const [swapActive, setSwapActive] = useState(false);
  const swapTimerRef = useRef<number | null>(null);
  const items = pieceSets[look].items;
  const selected = slots.map(index => items[index]);
  const total = selected.reduce((sum, item) => sum + item.price, 0);

  useEffect(() => () => {
    if (swapTimerRef.current !== null) window.clearTimeout(swapTimerRef.current);
  }, []);

  function chooseLook(next: "quiet" | "gorp") {
    if (swapTimerRef.current !== null) window.clearTimeout(swapTimerRef.current);
    swapTimerRef.current = null;
    setLook(next);
    setSlots(next === "quiet" ? [3, 4, 1, 5] : [0, 2, 4, 5]);
    setLocked([false, false, false, false]);
    setSwapping(null);
    setSwapActive(false);
  }

  function swapSlot(slot: number) {
    if (locked[slot] || swapping !== null) return;
    const unused = items.findIndex((_, index) => !slots.includes(index));
    const next = unused >= 0 ? unused : (slots[slot] + 1) % items.length;
    const update = () => setSlots(current => current.map((currentIndex, index) => index === slot ? next : currentIndex));
    if (reducedMotion) {
      update();
      return;
    }
    setSwapping({ slot, previous: selected[slot] });
    setSwapActive(false);
    update();
    window.requestAnimationFrame(() => setSwapActive(true));
    swapTimerRef.current = window.setTimeout(() => {
      setSwapping(null);
      setSwapActive(false);
      swapTimerRef.current = null;
    }, 420);
  }

  function toggleLock(slot: number) {
    if (swapping !== null) return;
    setLocked(current => current.map((value, index) => index === slot ? !value : value));
  }

  return (
    <div className="outfit-panel">
      <div className="outfit-toolbar">
        <div className="outfit-style" role="group" aria-label="Choose an outfit style">
          <button type="button" aria-pressed={look === "quiet"} onClick={() => chooseLook("quiet")}>Quiet luxury</button>
          <button type="button" aria-pressed={look === "gorp"} onClick={() => chooseLook("gorp")}>Gorpcore</button>
        </div>
        <span className="outfit-name">{look === "quiet" ? "A quieter day" : "City-ready outdoors"}</span>
      </div>
      <div className="outfit-grid">
        {selected.map((item, slot) => (
          <figure
            className={[
              "outfit-item",
              locked[slot] ? "is-locked" : "",
              swapping?.slot === slot ? "is-crossfading" : "",
              swapping?.slot === slot && swapActive ? "is-crossfading-active" : "",
            ].filter(Boolean).join(" ")}
            key={`${look}-${slot}`}
          >
            <div className="outfit-photo">
              {swapping?.slot === slot && (
                <img className="outfit-image-previous" src={swapping.previous.src} alt="" aria-hidden="true" />
              )}
              <img
                className="outfit-image-current"
                src={item.src}
                srcSet={srcSetFor(item.src)}
                sizes="(max-width: 720px) 45vw, 560px"
                alt={`${displayBrand(item.brand)} ${displayTitle(item.title)}`}
                width={item.w}
                height={item.h}
                loading="lazy"
                decoding="async"
              />
              <div className="outfit-controls">
                <button
                  className="outfit-control"
                  type="button"
                  aria-pressed={locked[slot]}
                  aria-label={`${locked[slot] ? "Unlock" : "Lock"} ${displayBrand(item.brand)} ${displayTitle(item.title)}`}
                  onClick={() => toggleLock(slot)}
                  disabled={swapping !== null}
                >
                  {locked[slot] ? "Locked" : "Lock"}
                </button>
                <button
                  className="outfit-control"
                  type="button"
                  aria-label={`Swap ${displayBrand(item.brand)} ${displayTitle(item.title)}`}
                  onClick={() => swapSlot(slot)}
                  disabled={locked[slot] || swapping !== null}
                >
                  Swap
                </button>
              </div>
            </div>
            <figcaption className="outfit-caption">
              <span className="outfit-brand">{displayBrand(item.brand)}</span>
              <span className="outfit-title">{displayTitle(item.title)}</span>
              <span className="outfit-price">{formatPrice(item.price)}</span>
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="outfit-total">
        <div>
          <span className="total-label">Outfit total</span>
          <strong>{formatPrice(total)}</strong>
        </div>
        <a className="button button-secondary" href="#waitlist">Join the waitlist</a>
      </div>
    </div>
  );
}

function OutfitSection() {
  return (
    <section className="outfit-section section-space" id="outfits" aria-labelledby="outfit-title">
      <div className="section-heading page-width">
        <div>
          <h2 id="outfit-title">A full look, already pulled together.</h2>
          <p>Keep the pieces you love, swap the rest, and see the total before you shop.</p>
        </div>
      </div>
      <OutfitPanel />
    </section>
  );
}

function ShopChips({ items, decorative = false }: { items: Piece[]; decorative?: boolean }) {
  return (
    <div className="shop-chip-set" role={decorative ? undefined : "list"}>
      {items.map((item, index) => (
        <div className="shop-chip" role={decorative ? undefined : "listitem"} key={`${item.shop}-${index}`}>
          <img
            src={item.src}
            srcSet={srcSetFor(item.src)}
            sizes="(max-width: 720px) 45vw, 560px"
            alt={decorative ? "" : displayTitle(item.title)}
            width={item.w}
            height={item.h}
            loading="lazy"
            decoding="async"
          />
          <span className="shop-chip-copy">
            <strong>{displayShopName(item.shop)}</strong>
            <span>{formatPrice(item.price)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function ShopsSection() {
  return (
    <section className="shops-section section-space" id="shops" aria-labelledby="shops-title">
      <div className="section-heading page-width">
        <div>
          <h2 id="shops-title">Good pieces, good shops.</h2>
          <p>New finds and second-hand listings, all in one place.</p>
        </div>
      </div>
      <div className="shop-marquee" role="region" aria-label="A selection of shops and prices">
        <div className="shop-track">
          <ShopChips items={shopPieces} />
          <ShopChips items={shopPieces} decorative />
        </div>
      </div>
      <p className="shops-note page-width">Carhartt WIP is available through Goodhood and Oi Polloi. All prices are in GBP.</p>
    </section>
  );
}

function FoundSection() {
  return (
    <section className="found-section section-space" aria-labelledby="found-title">
      <div className="section-heading page-width">
        <div>
          <h2 id="found-title">Found on Quenya.</h2>
          <p>A few pieces that might make it into someone&apos;s next look.</p>
        </div>
      </div>
      <div className="found-strip page-width">
        {people.map((item, index) => (
          <figure className="found-card" key={item.src} style={{ "--card-tilt": `${index % 2 === 0 ? -2.4 : 1.8}deg` } as CSSProperties}>
            <img
              src={item.src}
              srcSet={srcSetFor(item.src)}
              sizes="(max-width: 720px) 76vw, 270px"
              alt={`${displayBrand(item.brand)} ${displayTitle(item.title)}`}
              width={item.w}
              height={item.h}
              loading="lazy"
              decoding="async"
            />
            <figcaption>
              <span>{displayBrand(item.brand)}</span>
              <strong>{displayTitle(item.title)}</strong>
              <span className="found-price">{formatPrice(item.price)}</span>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

function FAQSection() {
  return (
    <section className="faq-section section-space" id="faq" aria-labelledby="faq-title">
      <div className="faq-layout page-width">
        <div className="section-heading">
          <div>
            <h2 id="faq-title">A few questions.</h2>
            <p>What to know before you join the waitlist.</p>
          </div>
        </div>
        <div className="faq-list">
          {faq.map(entry => (
            <details className="faq-item" key={entry.q}>
              <summary>
                <span>{entry.q}</span>
                <span className="faq-icon" aria-hidden="true" />
              </summary>
              <div className="faq-answer">
                <div><p>{entry.a}</p></div>
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

function WaitlistSection() {
  return (
    <section className="waitlist-section section-space" id="waitlist" aria-labelledby="waitlist-title">
      <div className="waitlist-panel page-width">
        <div className="waitlist-copy">
          <h2 id="waitlist-title">Come find your next look.</h2>
          <p>
            Quenya is in invite-only testing. Leave your email and we&apos;ll let you know when there&apos;s a place.
            An iPhone app is in development, so tell us what you&apos;d test on.
          </p>
        </div>
        <WaitlistForm />
      </div>
    </section>
  );
}

export function Landing() {
  return (
    <Page home>
      <Hero />
      <LearningSection />
      <OutfitSection />
      <ShopsSection />
      <FoundSection />
      <FAQSection />
      <WaitlistSection />
    </Page>
  );
}
