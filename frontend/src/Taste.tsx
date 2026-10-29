import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ErrorBox, gbpText, Loading, pretty, uniqueTasteEntries } from "./ui";
import type { Board, TaskStats, TasteState } from "./types";

const TASK_LABEL: Record<string, string> = {
  tagger: "Listing tagger", taste: "Taste updater", stylist: "Outfit stylist", reader: "Photo reader", router: "Board router", decoder: "Style decoder",
};

export function TasteScreen({ taste, board, refresh, onUpload, onReset, isAdmin = false }: {
  taste: TasteState | null; board: Board | null; refresh: () => void; onUpload: (f: File) => Promise<void>; onReset: () => void; isAdmin?: boolean;
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
  const core = taste?.core;
  const coreLikes = core ? uniqueTasteEntries([
    ...Object.entries(core.likes).map(([k, v]) => [k, v] as const),
    ...Object.entries(core.avoids).map(([k, v]) => [k, -v] as const),
  ], 8) : [];
  const shown = bars.length > 10 ? [...bars.slice(0, 7), ...bars.slice(-3)] : bars;
  return (
    <section className="screen">
      <p className="eyebrow">{board ? board.label : "Your taste"}{taste?.current ? ` · v${taste.current.version}` : ""}</p>
      <h1 className="h1">What we've learned.</h1>
      {!taste && <Loading>Loading your profile</Loading>}
      {taste && !p && <p className="muted">No profile yet. Start with a brief.</p>}
      {p && (
        <>
          <p className="summary">{gbpText(p.summary)}</p>
          {core && (core.rules.length > 0 || coreLikes.length > 0 || core.palette.length > 0) && (
            <div className="panel core"><span className="lbl">Across your boards <em>applies everywhere</em></span>
              {core.rules.length > 0 && <ul className="rules">{core.rules.map(r => <li key={r.rule}>{gbpText(r.rule)}<span className="from"> · {r.boards.join(", ")}</span></li>)}</ul>}
              <div className="chips">
                {coreLikes.map(([k, v]) => <span key={k} className={"chip static " + (v > 0 ? "pos" : "neg")}>{v > 0 ? "+" : "−"} {pretty(k)}</span>)}
                {core.palette.map(c => <span key={c} className="chip static">{c}</span>)}
              </div>
            </div>
          )}
          <div className="panel"><span className="lbl">This board</span>
            {shown.map(([k, v]) => (
              <div key={k} className={"bar" + (v < 0 ? " neg" : "")}><span>{pretty(k)}</span><i style={{ width: `${Math.abs(v) * 100}%` }} /><em>{v > 0 ? "+" : "−"}{Math.abs(v).toFixed(1)}</em></div>
            ))}
          </div>
          {(p.rules.length > 0 || p.palette.length > 0) && (
            <div className="panel"><span className="lbl">Rules & palette</span>
              <ul className="rules">{p.rules.map(r => <li key={r}>{gbpText(r)}</li>)}</ul>
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
                {changesOf(h).length ? <ul>{changesOf(h).map((c, i) => <li key={i}>{c}</li>)}</ul> : <p className="small muted">First read of your brief.</p>}
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="panel model"><span className="lbl">AI tasks <em>measured by us</em></span>
        {statsErr !== null && <ErrorBox error={statsErr} onRetry={loadStats} />}
        {!stats && statsErr === null && <Loading>Loading</Loading>}
        {stats?.map(s => {
          const toward = s.calls_toward_training ?? s.ok;
          const target = s.first_training_at ?? 25;
          return (
            <div key={s.task} className="task">
              <div className="mrow"><span>{TASK_LABEL[s.task] ?? s.task}</span><b>{s.ok} ok{s.p50_latency_ms ? ` · ${(s.p50_latency_ms / 1000).toFixed(1)}s p50` : ""}</b></div>
              <div className="train"><i style={{ width: `${Math.min(100, (toward / target) * 100)}%` }} /></div>
              {toward < target && <p className="small muted">{toward} / {target} successful calls toward first training</p>}
            </div>
          );
        })}
        <p className="small muted">AI tasks handle tagging, taste updates, outfit building and photo reading. Your corrections are used to personalize later suggestions.</p>
      </div>

      {isAdmin && <a className="admin-entry" href="/admin">Admin dashboard <span>→</span></a>}
      <button className="btn ghost wide danger" onClick={onReset}>Reset all boards</button>
    </section>
  );
}

function changesOf(h: TasteState["history"][number]): string[] {
  return h.changes ?? h.profile?.changes ?? [];
}
