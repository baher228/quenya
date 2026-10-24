import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ErrorBox, Loading } from "./ui";
import type { TaskStats, TasteState } from "./types";

const TASK_LABEL: Record<string, string> = {
  tagger: "Listing tagger", taste: "Taste updater", stylist: "Outfit stylist", reader: "Photo reader",
};

export function TasteScreen({ taste, refresh, onUpload, onReset }: {
  taste: TasteState | null; refresh: () => void; onUpload: (f: File) => Promise<void>; onReset: () => void;
}) {
  const [stats, setStats] = useState<TaskStats[] | null>(null);
  const [statsErr, setStatsErr] = useState<unknown>(null);
  const [uploading, setUploading] = useState(false);
  const [upErr, setUpErr] = useState<unknown>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadStats = () => api.stats().then(s => { setStats(s); setStatsErr(null); }).catch(setStatsErr);
  useEffect(() => { refresh(); void loadStats(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const p = taste?.current?.profile;
  const bars = p ? [
    ...Object.entries(p.likes).map(([k, v]) => [k, v] as const),
    ...Object.entries(p.avoids).map(([k, v]) => [k, -v] as const),
  ].sort((a, b) => b[1] - a[1]) : [];
  const shown = bars.length > 10 ? [...bars.slice(0, 7), ...bars.slice(-3)] : bars;

  return (
    <section className="screen">
      <p className="eyebrow">Your taste{taste?.current ? ` · v${taste.current.version}` : ""}</p>
      <h1 className="h1">What we've learned.</h1>
      {!taste && <Loading>Loading your profile</Loading>}
      {taste && !p && <p className="muted">No profile yet. Start with a brief.</p>}
      {p && (
        <>
          <p className="summary">{p.summary}</p>
          <div className="panel"><span className="lbl">Weights</span>
            {shown.map(([k, v]) => (
              <div key={k} className={"bar" + (v < 0 ? " neg" : "")}><span>{k}</span><i style={{ width: `${Math.abs(v) * 100}%` }} /><em>{v > 0 ? "+" : "−"}{Math.abs(v).toFixed(1)}</em></div>
            ))}
          </div>
          {(p.rules.length > 0 || p.palette.length > 0) && (
            <div className="panel"><span className="lbl">Rules & palette</span>
              <ul className="rules">{p.rules.map(r => <li key={r}>{r}</li>)}</ul>
              <div className="chips">{p.palette.map(c => <span key={c} className="chip static">{c}</span>)}</div>
              <div className="mrow"><span>Budget ceiling</span><b>£{Math.round(p.price_ceiling_gbp)}</b></div>
              <div className="mrow"><span>Condition</span><b>{p.condition_preference}</b></div>
            </div>
          )}
        </>
      )}

      <div className="panel"><span className="lbl">Inspiration photo</span>
        <p className="small muted">Upload a look you like. The photo reader picks out garments, palette and era, then your taste updates.</p>
        {upErr !== null && <ErrorBox error={upErr} />}
        <button className="btn ghost wide" disabled={uploading || !p} onClick={() => fileRef.current?.click()}>{uploading ? "Reading photo…" : "+ Add a photo"}</button>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={async e => {
          const f = e.target.files?.[0]; e.target.value = ""; if (!f) return;
          setUploading(true); setUpErr(null);
          try { await onUpload(f); void loadStats(); } catch (err) { setUpErr(err); } finally { setUploading(false); }
        }} />
      </div>

      {taste && taste.history.length > 0 && (
        <div className="panel"><span className="lbl">Model updates</span>
          <ol className="history">
            {[...taste.history].reverse().map(h => (
              <li key={h.version}>
                <div className="h-top"><b>v{h.version}</b><span className="mono small muted">{h.request_id ?? ""}</span></div>
                {h.changes.length ? <ul>{h.changes.map((c, i) => <li key={i}>{c}</li>)}</ul> : <p className="small muted">First read of your brief.</p>}
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="panel model"><span className="lbl">Mubit tasks <em>measured by us</em></span>
        {statsErr !== null && <ErrorBox error={statsErr} onRetry={loadStats} />}
        {!stats && statsErr === null && <Loading>Loading</Loading>}
        {stats?.map(s => {
          const toward = s.calls_toward_training ?? s.ok;
          const target = s.first_training_at ?? 25;
          return (
            <div key={s.task} className="task">
              <div className="mrow"><span>{TASK_LABEL[s.task] ?? s.task}</span><b>{s.ok} ok{s.p50_latency_ms ? ` · ${(s.p50_latency_ms / 1000).toFixed(1)}s p50` : ""}</b></div>
              <div className="train"><i style={{ width: `${Math.min(100, (toward / target) * 100)}%` }} /></div>
              <p className="small muted">{toward >= target ? `${toward} successful calls, past the ${target}-example training threshold. Adapter status lives in the Mubit console.` : `${toward} / ${target} successful calls toward first training`}</p>
            </div>
          );
        })}
        <p className="small muted">Every call goes through Mubit with a fixed prompt per task, and your corrections are sent as calls of the same task.</p>
      </div>

      <button className="btn ghost wide danger" onClick={onReset}>Reset demo profile</button>
    </section>
  );
}
