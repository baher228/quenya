import { useCallback, useEffect, useState } from "react";
import { api, errText, gbp, img } from "./api";
import { BriefScreen, DEFAULT_BRIEF } from "./Brief";
import { DeckScreen } from "./Deck";
import { FindScreen } from "./Find";
import { OutfitsScreen, REASONS } from "./Outfits";
import { TasteScreen } from "./Taste";
import { ErrorBox, Loading, Provenance } from "./ui";
import type { Board, Brief, Checkout, Item, Outfit, Route, TasteState, TasteVersion, UploadResult } from "./types";

type Tab = "brief" | "swipe" | "outfits" | "find" | "taste";
type Sheet =
  | { kind: "reject"; outfit: Outfit }
  | { kind: "item"; item: Item }
  | { kind: "checkout"; outfit: Outfit; result?: Checkout; error?: unknown }
  | { kind: "move"; route: Route; board: Board }
  | { kind: "photo"; up: UploadResult };

interface RouteNote { route: Route; board: Board }

interface Toast { id: number; title: string; lines: string[] }

export default function App() {
  const [tab, setTab] = useState<Tab>("brief");
  const [brief, setBrief] = useState<Brief>(DEFAULT_BRIEF);
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState<unknown>(null);
  const [fetchNote, setFetchNote] = useState<string | null>(null);
  const [taste, setTaste] = useState<TasteState | null>(null);
  const [outfits, setOutfits] = useState<Outfit[]>([]);
  const [outLoading, setOutLoading] = useState(false);
  const [outErr, setOutErr] = useState<unknown>(null);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [deckKey, setDeckKey] = useState(0);
  const [updating, setUpdating] = useState(false);
  const [boards, setBoards] = useState<Board[]>([]);
  const [routeNote, setRouteNote] = useState<RouteNote | null>(null);
  const [briefKey, setBriefKey] = useState(0);
  const [picking, setPicking] = useState(true);
  const activeBoard = boards.find(b => b.active) ?? null;
  const refreshBoards = useCallback(() => { api.boards().then(setBoards).catch(() => undefined); }, []);
  useEffect(refreshBoards, [refreshBoards]);
  useEffect(() => { if (!routeNote) return; const h = window.setTimeout(() => setRouteNote(null), 15000); return () => window.clearTimeout(h); }, [routeNote]);

  const version = taste?.current?.version ?? null;
  const refreshTaste = useCallback(() => { api.taste().then(setTaste).catch(() => setTaste({ current: null, history: [], counts: {} })); }, []);
  useEffect(refreshTaste, [refreshTaste]);

  const announce = useCallback((t: TasteVersion | null | undefined, why: string) => {
    if (!t) return;
    setToast({ id: Date.now(), title: `Taste v${t.version} · ${why}`, lines: (t.changes ?? t.profile?.changes ?? []).slice(0, 3) });
    refreshTaste();
  }, [refreshTaste]);
  useEffect(() => { if (!toast) return; const h = window.setTimeout(() => setToast(null), 6000); return () => window.clearTimeout(h); }, [toast]);

  const runUpdate = useCallback(async (why: string) => {
    setUpdating(true);
    try { announce(await api.updateTaste(), why); }
    catch (e) { if (!(e instanceof Error && "status" in e && (e as { status: number }).status === 409)) setToast({ id: Date.now(), title: "Taste update failed", lines: [errText(e)] }); }
    finally { setUpdating(false); }
  }, [announce]);

  const boardChanged = useCallback(() => {
    setOutfits([]); setDeckKey(k => k + 1); refreshTaste(); refreshBoards();
  }, [refreshTaste, refreshBoards]);

  const handleUpload = useCallback(async (f: File) => {
    const r = await api.upload(f);
    if (r.pending) setSheet({ kind: "photo", up: r });
    else announce(r.taste, "read your photo");
  }, [announce]);

  const switchBoard = async (b: Board) => {
    if (b.active) return true;
    try { await api.activate(b.id); setBrief({ ...DEFAULT_BRIEF, ...b.brief }); setBriefKey(k => k + 1); boardChanged(); setRouteNote(null); return true; }
    catch (e) { setToast({ id: Date.now(), title: "Couldn't switch board", lines: [errText(e)] }); return false; }
  };

  const newBoard = () => { setBrief({ ...DEFAULT_BRIEF, text: "" }); setBriefKey(k => k + 1); setTab("brief"); };

  const moveRoute = async (route: Route, boardId: string | null) => {
    setSheet(null);
    try {
      const r = await api.correctRoute(route.id, boardId);
      setRouteNote({ route: { ...r.route, routed: "corrected" }, board: r.board });
      boardChanged();
      if (r.taste && "changes" in r.taste && r.taste.changes?.length) announce(r.taste, "routing fixed");
    } catch (e) { setToast({ id: Date.now(), title: "Couldn't move it", lines: [errText(e)] }); }
  };

  const assignPhoto = async (up: UploadResult, boardId: string | null, label?: string) => {
    setSheet(null);
    try {
      const r = await api.assignUpload(up.upload_id, boardId, label);
      if (!activeBoard || r.board.id !== activeBoard.id) boardChanged(); else refreshBoards();
      announce(r.taste, `photo added to ${r.board.label}`);
    } catch (e) { setToast({ id: Date.now(), title: "Couldn't file that photo", lines: [errText(e)] }); }
  };

  const submitBrief = async (b: Brief, photos: File[]) => {
    setCreating(true); setCreateErr(null); setBrief(b);
    try {
      const r = await api.createProfile(b);
      setOutfits([]);
      refreshTaste(); refreshBoards();
      if (r.route && r.board) setRouteNote({ route: r.route, board: r.board });
      for (const f of photos) {
        try { await handleUpload(f); } catch (e) { setToast({ id: Date.now(), title: "Couldn't read a photo", lines: [errText(e)] }); }
      }
      if (r.fetch_job_id) void pollFetch(r.fetch_job_id);
      setDeckKey(k => k + 1);
      setPicking(false);
      setTab("swipe");
    } catch (e) { setCreateErr(e); } finally { setCreating(false); }
  };

  const pollFetch = async (id: string) => {
    for (let i = 0; i < 60; i++) {
      try {
        const j = await api.fetchJob(id);
        setFetchNote(j.status === "running" ? `Fetching fresh listings · ${j.tagged}/${j.fetched} tagged` : j.status === "done" ? `${j.tagged} fresh listings added` : "Live fetch failed; using the saved pool");
        if (j.status !== "running") { window.setTimeout(() => setFetchNote(null), 5000); return; }
      } catch { setFetchNote(null); return; }
      await new Promise(r => window.setTimeout(r, 2000));
    }
  };

  const build = useCallback(async () => {
    setTab("outfits"); setOutLoading(true); setOutErr(null);
    try { setOutfits((await api.outfits(3)).outfits); } catch (e) { setOutErr(e); } finally { setOutLoading(false); }
  }, []);

  const deckDone = async () => { await runUpdate("from your swipes"); void build(); };

  const replace = (old: Outfit, next: Outfit) => setOutfits(os => os.map(o => (o.id === old.id ? next : o)));

  const like = async (o: Outfit) => {
    try { const r = await api.verdict(o.id, "like"); replace(o, { ...o, ...r.outfit, pieces: r.outfit.pieces ?? o.pieces, verdict: "like" }); announce(r.taste, "you loved an outfit"); }
    catch (e) { setToast({ id: Date.now(), title: "Couldn't save that", lines: [errText(e)] }); }
  };

  const reject = async (o: Outfit, reason: string) => {
    setSheet(null);
    replace(o, { ...o, verdict: "reject", reason });
    try { const r = await api.verdict(o.id, "reject", reason); announce(r.taste, "correction applied"); }
    catch (e) { replace(o, o); setToast({ id: Date.now(), title: "Correction not saved", lines: [errText(e)] }); }
  };

  const checkout = async (o: Outfit) => {
    setSheet({ kind: "checkout", outfit: o });
    try { const r = await api.checkout(o.id); setSheet({ kind: "checkout", outfit: o, result: r }); }
    catch (e) { setSheet({ kind: "checkout", outfit: o, error: e }); }
  };

  const reset = async () => {
    if (!window.confirm("Reset the demo? All boards, swipes, outfits and taste versions are deleted.")) return;
    await api.reset().catch(() => undefined);
    setBoards([]); setRouteNote(null); setBriefKey(k => k + 1);
    setOutfits([]); setTaste({ current: null, history: [], counts: {} }); setBrief(DEFAULT_BRIEF); setTab("brief");
  };

  const hasProfile = !!taste?.current;
  return (
    <div className="stage">
      <div className="phone">
        <header className="top">
          <span className="logo">quenya<i>.</i></span>
          <span className="top-r">{updating ? "Updating taste…" : fetchNote ?? (version ? `taste v${version}` : "")}</span>
        </header>
        {boards.length > 0 && tab !== "swipe" && (
          <div className="boards" role="tablist" aria-label="Boards">
            {boards.map(b => (
              <button key={b.id} role="tab" aria-selected={b.active} className={"board" + (b.active ? " on" : "")} onClick={() => switchBoard(b)}>
                {b.label}{b.version ? <em>v{b.version}</em> : null}
              </button>
            ))}
            <button className="board add" onClick={newBoard} aria-label="New brief">+</button>
          </div>
        )}
        {routeNote && (
          <div className="route" role="status">
            <p>
              <b>{routeNote.route.routed === "new" ? `New board · ${routeNote.board.label}` : routeNote.route.routed === "existing" ? `Added to ${routeNote.board.label}` : `Moved to ${routeNote.board.label}`}</b>
              <span>{routeNote.route.routed === "corrected" ? "Saved as a routing correction. Next time the router sees a brief like this, it gets this example." : routeNote.route.reason}</span>
            </p>
            {routeNote.route.routed !== "corrected" && <button className="btn ghost sm" onClick={() => setSheet({ kind: "move", route: routeNote.route, board: routeNote.board })}>Move</button>}
            <button className="x" onClick={() => setRouteNote(null)} aria-label="Dismiss">×</button>
          </div>
        )}
        <main className="body">
          {tab === "brief" && (
            <>
              <BriefScreen key={briefKey} initial={brief} busy={creating} onSubmit={submitBrief} hasBoards={boards.length > 0} />
              {createErr !== null && <ErrorBox error={createErr} />}
            </>
          )}
          {tab === "swipe" && (hasProfile && !picking
            ? <DeckScreen key={deckKey} profile={taste?.current?.profile ?? null} version={version}
                board={activeBoard?.label ?? ""} onChange={() => setPicking(true)}
                onSignal={due => { if (due && !updating) void runUpdate("from your swipes"); }}
                onDone={deckDone} onOpenItem={item => setSheet({ kind: "item", item })} />
            : boards.length > 0
              ? <StylePicker boards={boards} onNew={newBoard} onPick={async b => { if (await switchBoard(b)) setPicking(false); }} />
              : <NeedsBrief go={() => setTab("brief")} />)}
          {tab === "outfits" && (hasProfile
            ? <OutfitsScreen outfits={outfits} loading={outLoading} error={outErr} version={version} onBuild={build}
                onReplace={replace} onReject={o => setSheet({ kind: "reject", outfit: o })} onLike={like}
                onCheckout={checkout} onOpenItem={item => setSheet({ kind: "item", item })} />
            : <NeedsBrief go={() => setTab("brief")} />)}
          {tab === "find" && <FindScreen onOpenItem={item => setSheet({ kind: "item", item })} />}
          {tab === "taste" && <TasteScreen taste={taste} board={activeBoard} refresh={refreshTaste} onReset={reset} onUpload={handleUpload} />}
        </main>

        {toast && (
          <div className="toast" key={toast.id} role="status" onClick={() => { setToast(null); setTab("taste"); }}>
            <b>{toast.title}</b>
            {toast.lines.map((l, i) => <span key={i}>{l}</span>)}
          </div>
        )}

        <nav className="tabs">
          {([["brief", "Brief"], ["swipe", "Swipe"], ["outfits", "Outfits"], ["find", "Find"], ["taste", "Taste"]] as [Tab, string][]).map(([t, l]) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => { if (t === "swipe") setPicking(true); setTab(t); }}>{l}</button>
          ))}
        </nav>

        <div className={"sheet" + (sheet ? " open" : "")} onClick={e => { if (e.target === e.currentTarget) setSheet(null); }}>
          <div className="sheet-in">
            {sheet?.kind === "reject" && <RejectSheet onPick={r => reject(sheet.outfit, r)} onClose={() => setSheet(null)} />}
            {sheet?.kind === "item" && <ItemSheet item={sheet.item} onClose={() => setSheet(null)} onCorrected={t => announce(t, "tag correction")} />}
            {sheet?.kind === "checkout" && <CheckoutSheet s={sheet} onClose={() => setSheet(null)} />}
            {sheet?.kind === "move" && <MoveSheet route={sheet.route} current={sheet.board} boards={boards} onPick={id => moveRoute(sheet.route, id)} onClose={() => setSheet(null)} />}
            {sheet?.kind === "photo" && <PhotoSheet up={sheet.up} boards={boards} active={activeBoard} onPick={(id, label) => assignPhoto(sheet.up, id, label)} onClose={() => setSheet(null)} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function StylePicker({ boards, onPick, onNew }: { boards: Board[]; onPick: (b: Board) => void; onNew: () => void }) {
  return (
    <section className="screen pick">
      <p className="eyebrow">Swipe</p>
      <p className="h3">Which style today?</p>
      <div className="pick-list">
        {boards.map(b => (
          <button key={b.id} className={"pick-row" + (b.active ? " on" : "")} onClick={() => onPick(b)}>
            <span className="pick-t">{b.label}</span>
            <span className="pick-m">{b.context}{b.version ? ` · taste v${b.version}` : ""} · {b.counts.swipes} swiped</span>
            <span className="pick-go" aria-hidden="true">→</span>
          </button>
        ))}
        <button className="pick-row add" onClick={onNew}>
          <span className="pick-t">New style</span>
          <span className="pick-m">Write a brief</span>
          <span className="pick-go" aria-hidden="true">+</span>
        </button>
      </div>
    </section>
  );
}

function NeedsBrief({ go }: { go: () => void }) {
  return (
    <section className="screen empty">
      <p className="h3">Start with a brief.</p>
      <p className="muted">Say what you're after first. Swipes and outfits come from it.</p>
      <button className="btn primary" onClick={go}>Write a brief</button>
    </section>
  );
}

function RejectSheet({ onPick, onClose }: { onPick: (r: string) => void; onClose: () => void }) {
  const [own, setOwn] = useState("");
  return (
    <>
      <p className="h3">What's off?</p>
      <p className="small muted">Your answer is a correction: your taste updates now, and it's sent to Mubit as a call of the same taste task.</p>
      {REASONS.map(r => <button key={r} className="reason" onClick={() => onPick(r)}>{r}</button>)}
      <form className="own" onSubmit={e => { e.preventDefault(); if (own.trim()) onPick(own.trim()); }}>
        <input value={own} onChange={e => setOwn(e.target.value)} placeholder="Or say it yourself: “no square-toe shoes”" maxLength={200} />
        <button className="btn primary" disabled={!own.trim()}>Send</button>
      </form>
      <button className="close" onClick={onClose}>Cancel</button>
    </>
  );
}

function ItemSheet({ item, onClose, onCorrected }: { item: Item; onClose: () => void; onCorrected: (t: TasteVersion | null | undefined) => void }) {
  const [tags, setTags] = useState(item.tags);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [rid, setRid] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.correctTags(item.id, note.trim()); setTags(r.tags); setRid(r.request_id); setNote(""); onCorrected(r.taste); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <>
      <div className="item-head">
        <img src={img(item)} alt={item.title} />
        <div>
          <p className="brand">{item.brand || "Unbranded"}</p>
          <p className="h3">{item.title}</p>
          <p><b>{gbp(item.price_gbp)}</b>{item.size ? ` · size ${item.size}` : ""}</p>
          <Provenance source={item.source} origin={item.origin} condition={tags?.condition ?? item.condition} />
        </div>
      </div>
      {tags && (
        <div className="tagtable">
          <span>Read as</span><p>{tags.one_line}</p>
          <span>Category</span><p>{tags.slot} · {tags.category}</p>
          <span>Style</span><p>{tags.aesthetics.join(", ")}</p>
          <span>Colour</span><p>{tags.colours.join(", ")}{tags.era !== "unknown" ? ` · ${tags.era}` : ""}</p>
        </div>
      )}
      <form className="own" onSubmit={e => { e.preventDefault(); if (note.trim()) void submit(); }}>
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="Fix a tag: “this is 90s, not y2k”" maxLength={300} />
        <button className="btn primary" disabled={busy || !note.trim()}>{busy ? "…" : "Fix"}</button>
      </form>
      {rid && <p className="small muted mono">Re-tagged with your correction · {rid}</p>}
      {err !== null && <ErrorBox error={err} />}
      <a className="btn primary wide center" href={item.source_url} target="_blank" rel="noreferrer">View listing on {item.source === "vinted" ? "Vinted" : new URL(item.source_url).hostname.replace("www.", "")}</a>
      <button className="close" onClick={onClose}>Close</button>
    </>
  );
}

function CheckoutSheet({ s, onClose }: { s: Extract<Sheet, { kind: "checkout" }>; onClose: () => void }) {
  return (
    <>
      <p className="h3">{s.outfit.title}</p>
      {!s.result && !s.error && <Loading>Preparing your bundle</Loading>}
      {s.error !== undefined && <ErrorBox error={s.error} />}
      {s.result && (
        <>
          <p className="badge">Demo checkout · no payment taken · {s.result.order_ref}</p>
          <p className="small muted">For real orders we'd buy each piece from its shop and ship the bundle to you. For now, here's every listing.</p>
          <ul className="lines">
            {s.result.lines.map(l => (
              <li key={l.source_url}><a href={l.source_url} target="_blank" rel="noreferrer">{l.title}</a><span>{l.source} · {gbp(l.price_gbp)}</span></li>
            ))}
          </ul>
          <div className="mrow"><span>Total</span><b>{gbp(s.result.total_gbp)}</b></div>
        </>
      )}
      <button className="close" onClick={onClose}>Done</button>
    </>
  );
}

function MoveSheet({ route, current, boards, onPick, onClose }: {
  route: Route; current: Board; boards: Board[]; onPick: (id: string | null) => void; onClose: () => void;
}) {
  const others = boards.filter(b => b.id !== current.id);
  return (
    <>
      <p className="h3">Where does this belong?</p>
      <p className="small muted">The router filed it under {current.label}. Your answer is a routing correction: it's stored and shown to the router with every later brief.</p>
      {others.map(b => (
        <button key={b.id} className="reason" onClick={() => onPick(b.id)}>{b.label}<span className="ctx">{b.context}</span></button>
      ))}
      {route.routed === "existing" && <button className="reason" onClick={() => onPick(null)}>Its own new board</button>}
      {others.length === 0 && route.routed !== "existing" && <p className="small muted">No other boards yet.</p>}
      <button className="close" onClick={onClose}>Cancel</button>
    </>
  );
}

function PhotoSheet({ up, boards, active, onPick, onClose }: {
  up: UploadResult; boards: Board[]; active: Board | null; onPick: (id: string | null, label?: string) => void; onClose: () => void;
}) {
  const match = boards.find(b => b.label.toLowerCase() === up.route.label.toLowerCase() && !b.active);
  return (
    <>
      <p className="h3">This looks like {up.route.label}.</p>
      <p className="small muted">{up.read.one_line}</p>
      <p className="small muted">{up.route.reason}</p>
      {match
        ? <button className="reason" onClick={() => onPick(match.id)}>Add to {match.label}</button>
        : <button className="reason" onClick={() => onPick(null, up.route.label)}>Start a “{up.route.label}” board</button>}
      {active && <button className="reason" onClick={() => onPick(active.id)}>Keep it on {active.label}</button>}
      <button className="close" onClick={onClose}>Decide later</button>
    </>
  );
}
