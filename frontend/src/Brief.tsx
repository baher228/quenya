import { useRef, useState } from "react";
import { Seg } from "./ui";
import type { Brief, Slot } from "./types";

const SLOTS: [Slot, string][] = [
  ["outerwear", "Outerwear"], ["top", "Tops"], ["knitwear", "Knitwear"], ["bottom", "Trousers & skirts"],
  ["dress", "Dresses"], ["shoes", "Shoes"], ["bag", "Bags"], ["accessory", "Accessories"],
];

const range = (start: number, end: number, step: number) =>
  Array.from({ length: (end - start) / step + 1 }, (_, index) => start + index * step);

export const BUDGET_STEPS = [
  ...range(50, 500, 10),
  ...range(525, 1000, 25),
  ...range(1100, 3000, 100),
  ...range(3250, 10000, 250),
];

function budgetIndex(value: number) {
  return BUDGET_STEPS.reduce(
    (nearest, step, index) =>
      Math.abs(step - value) < Math.abs(BUDGET_STEPS[nearest] - value)
        ? index
        : nearest,
    0,
  );
}

export const DEFAULT_BRIEF: Brief = {
  text: "Autumn in London. Workwear but cleaner, Japanese labels, things that age well. No big logos.",
  looking_for: "outfits",
  pieces: ["outerwear", "top", "knitwear", "bottom", "shoes"],
  include_shoes: true,
  gender: "menswear",
  condition: "both",
  niche: 0.7,
  budget_gbp: 300,
  sizes: { tops: "M", waist: "32", shoes: "9" },
};

export function BriefScreen({ initial, busy, onSubmit, mode, boardLabel, onNew }: {
  initial: Brief;
  busy: boolean;
  onSubmit: (b: Brief, photos: File[]) => void;
  mode: "new" | "edit";
  boardLabel?: string;
  onNew?: () => void;
}) {
  const [b, setB] = useState<Brief>(initial);
  const [photos, setPhotos] = useState<File[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const set = <K extends keyof Brief>(k: K, v: Brief[K]) => setB(prev => ({ ...prev, [k]: v }));
  const toggleSlot = (s: Slot) =>
    set("pieces", b.pieces.includes(s) ? b.pieces.filter(x => x !== s) : [...b.pieces, s]);
  const nicheWord = b.niche < 0.3 ? "mainstream" : b.niche < 0.6 ? "a bit off-piste" : b.niche < 0.85 ? "niche" : "deep cut";

  return (
    <section className="screen">
      <p className="eyebrow">{mode === "edit" ? "Edit style" : "New style"}</p>
      <h1 className="h1">{mode === "edit" ? boardLabel ?? "Edit style" : "Tell us what you're after."}</h1>

      <label className="field">
        <span className="lbl">In your words</span>
        <textarea rows={3} value={b.text} onChange={e => set("text", e.target.value)} maxLength={600}
          placeholder="e.g. Autumn in London. Workwear but cleaner, Japanese labels, no big logos." />
      </label>

      <div className="group"><span className="lbl">Dressing</span>
        <Seg value={b.gender} onChange={v => set("gender", v)}
          options={[["menswear", "Menswear"], ["womenswear", "Womenswear"], ["both", "Both"]]} /></div>

      <div className="group"><span className="lbl">Looking for</span>
        <Seg value={b.looking_for} onChange={v => set("looking_for", v)}
          options={[["outfits", "Outfits"], ["pieces", "Single pieces"]]} /></div>

      {b.looking_for === "outfits" && (
        <div className="group"><span className="lbl">Shoes</span>
          <Seg value={b.include_shoes ? "yes" : "no"} onChange={v => set("include_shoes", v === "yes")}
            options={[["yes", "Include shoes"], ["no", "Clothes only"]]} /></div>
      )}

      {b.looking_for === "pieces" && (
        <div className="group"><span className="lbl">Pieces</span>
          <div className="chips">
            {SLOTS.map(([s, label]) => (
              <button key={s} className={"chip" + (b.pieces.includes(s) ? " sel" : "")} aria-pressed={b.pieces.includes(s)} onClick={() => toggleSlot(s)}>{label}</button>
            ))}
          </div></div>
      )}

      <div className="group"><span className="lbl">Condition</span>
        <Seg value={b.condition} onChange={v => set("condition", v)}
          options={[["second-hand", "Second-hand"], ["new", "New"], ["both", "Both"]]} /></div>

      <div className="group"><span className="lbl">How niche <em>{nicheWord}</em></span>
        <input type="range" className="range" min={0} max={100} value={Math.round(b.niche * 100)}
          onChange={e => set("niche", Number(e.target.value) / 100)} aria-label="How niche" /></div>

      <div className="group"><span className="lbl">Budget per outfit <em>up to £{b.budget_gbp.toLocaleString("en-GB")}</em></span>
        <input type="range" className="range" min={0} max={BUDGET_STEPS.length - 1} step={1} value={budgetIndex(b.budget_gbp)}
          onChange={e => set("budget_gbp", BUDGET_STEPS[Number(e.target.value)])} aria-label="Budget per outfit" /></div>

      <div className="group"><span className="lbl">Sizes</span>
        <div className="sizes">
          {(["tops", "waist", "shoes"] as const).map(k => (
            <label key={k}><span>{k === "tops" ? "Tops" : k === "waist" ? "Waist" : "Shoes UK"}</span>
              <input value={b.sizes[k]} maxLength={8} onChange={e => set("sizes", { ...b.sizes, [k]: e.target.value })} /></label>
          ))}
        </div></div>

      <div className="group"><span className="lbl">Inspiration photos <em>optional</em></span>
        <div className="insp">
          {photos.map((f, i) => (
            <button key={i} className="insp-thumb" onClick={() => setPhotos(photos.filter((_, j) => j !== i))} aria-label="Remove photo">
              <img src={URL.createObjectURL(f)} alt="" />
            </button>
          ))}
          {photos.length < 3 && <button className="add" onClick={() => fileRef.current?.click()}>+ Add</button>}
          <input ref={fileRef} type="file" accept="image/*" hidden
            onChange={e => { const f = e.target.files?.[0]; if (f) setPhotos([...photos, f]); e.target.value = ""; }} />
        </div></div>

      <button className="btn primary wide" disabled={busy || b.text.trim().length < 3} onClick={() => onSubmit(b, photos)}>
        {busy ? (mode === "edit" ? "Updating…" : "Creating…") : (mode === "edit" ? "Update style" : "Create style")}
      </button>
      {mode === "edit" && <button className="small muted brief-new" onClick={onNew}>+ New style instead</button>}
    </section>
  );
}
