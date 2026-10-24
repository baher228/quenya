import { useRef, useState } from "react";
import { Seg } from "./ui";
import type { Brief, Slot } from "./types";

const SLOTS: [Slot, string][] = [
  ["outerwear", "Outerwear"], ["top", "Tops"], ["knitwear", "Knitwear"], ["bottom", "Trousers & skirts"],
  ["dress", "Dresses"], ["shoes", "Shoes"], ["bag", "Bags"], ["accessory", "Accessories"],
];

export const DEFAULT_BRIEF: Brief = {
  text: "Autumn in London. Workwear but cleaner, Japanese labels, things that age well. No big logos.",
  looking_for: "outfits",
  pieces: ["outerwear", "knitwear", "bottom", "shoes"],
  gender: "menswear",
  condition: "both",
  niche: 0.7,
  budget_gbp: 300,
  sizes: { tops: "M", waist: "32", shoes: "9" },
};

export function BriefScreen({ initial, busy, onSubmit }: {
  initial: Brief; busy: boolean; onSubmit: (b: Brief, photos: File[]) => void;
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
      <p className="eyebrow">New brief</p>
      <h1 className="h1">Tell us what you're after.</h1>

      <label className="field">
        <span className="lbl">In your words</span>
        <textarea rows={3} value={b.text} onChange={e => set("text", e.target.value)} maxLength={600} />
      </label>

      <div className="group"><span className="lbl">Dressing</span>
        <Seg value={b.gender} onChange={v => set("gender", v)}
          options={[["menswear", "Menswear"], ["womenswear", "Womenswear"], ["both", "Both"]]} /></div>

      <div className="group"><span className="lbl">Looking for</span>
        <Seg value={b.looking_for} onChange={v => set("looking_for", v)}
          options={[["outfits", "Outfits"], ["pieces", "Single pieces"]]} /></div>

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

      <div className="group"><span className="lbl">Budget per outfit <em>up to £{b.budget_gbp}</em></span>
        <input type="range" className="range" min={50} max={1000} step={10} value={b.budget_gbp}
          onChange={e => set("budget_gbp", Number(e.target.value))} aria-label="Budget per outfit" /></div>

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
        {busy ? "Reading your brief…" : "Start swiping"}
      </button>
    </section>
  );
}
