import { useState, type FormEvent } from "react";

type Device = "iphone" | "android" | "web";
type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; email: string }
  | { kind: "error"; message: string };

const DEVICES: { id: Device; label: string }[] = [
  { id: "iphone", label: "iPhone" },
  { id: "android", label: "Android" },
  { id: "web", label: "Web" },
];

function sourceTag(): string {
  const params = new URLSearchParams(window.location.search);
  const tag = params.get("utm_source") || params.get("ref");
  return tag ? `marketing:${tag}`.slice(0, 64) : "marketing";
}

function errorMessage(status: number, detail: unknown): string {
  if (status === 429) return "Too many signups from this connection. Give it a minute and try again.";
  if (status === 422 && Array.isArray(detail)) {
    const fields = detail.map((d: { loc?: unknown[] }) => String(d.loc?.[d.loc.length - 1] ?? ""));
    if (fields.includes("email")) return "That email address doesn't look right. Check it and try again.";
    if (fields.includes("consent")) return "Please tick the consent box so we can keep your details.";
    return "Some of those details are too long or not valid. Check them and try again.";
  }
  return "We couldn't save your signup just now. Please try again in a moment.";
}

export function WaitlistForm() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [devices, setDevices] = useState<Device[]>([]);

  const toggle = (id: Device) =>
    setDevices(d => (d.includes(id) ? d.filter(x => x !== id) : [...d, id]));

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const text = (k: string) => String(data.get(k) ?? "").trim() || null;
    const email = text("email") ?? "";
    setStatus({ kind: "sending" });
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          name: text("name"),
          handle: text("handle"),
          style_text: text("style_text"),
          wants_testing: data.get("wants_testing") === "on",
          devices,
          consent: data.get("consent") === "on",
          source: sourceTag(),
          website: text("website"),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus({ kind: "error", message: errorMessage(res.status, body?.detail) });
        return;
      }
      setStatus({ kind: "done", email });
    } catch {
      setStatus({ kind: "error", message: "We couldn't reach the server. Check your connection and try again." });
    }
  }

  if (status.kind === "done") {
    return (
      <div className="form-done" role="status" aria-live="polite">
        <p className="kicker">You're on the list</p>
        <p className="done-title">Thank you. We'll be in touch.</p>
        <p className="done-body">
          We've saved <strong>{status.email}</strong>. When a testing place opens up, the invite comes to that address.
          Signed up before? We've just updated your details.
        </p>
        <button type="button" className="link-btn" onClick={() => setStatus({ kind: "idle" })}>
          Sign up another email
        </button>
      </div>
    );
  }

  const sending = status.kind === "sending";
  return (
    <form className="form" onSubmit={submit} noValidate={false}>
      <div className="field">
        <label htmlFor="wl-email">Email <span className="req">required</span></label>
        <input id="wl-email" name="email" type="email" inputMode="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" />
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="wl-name">First name</label>
          <input id="wl-name" name="name" type="text" autoComplete="given-name" maxLength={80} />
        </div>
        <div className="field">
          <label htmlFor="wl-handle">Instagram or TikTok</label>
          <input id="wl-handle" name="handle" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={64} placeholder="@handle" />
        </div>
      </div>
      <div className="field">
        <label htmlFor="wl-style">Describe your style in a few words</label>
        <input id="wl-style" name="style_text" type="text" maxLength={280} placeholder="e.g. gorpcore but city-friendly" />
      </div>

      <fieldset className="field devices">
        <legend>What would you test on?</legend>
        <div className="chips">
          {DEVICES.map(d => (
            <label key={d.id} className="chip">
              <input type="checkbox" checked={devices.includes(d.id)} onChange={() => toggle(d.id)} />
              <span>{d.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="check">
        <input type="checkbox" name="wants_testing" defaultChecked />
        <span>I want to test the app early</span>
      </label>
      <label className="check">
        <input type="checkbox" name="consent" required />
        <span>
          I agree to Quenya storing these details to contact me about the waitlist and testing, as described in the{" "}
          <a href="/privacy">privacy notice</a>. <span className="req">required</span>
        </span>
      </label>

      <div className="hp" aria-hidden="true">
        <label htmlFor="wl-website">Website</label>
        <input id="wl-website" name="website" type="text" tabIndex={-1} autoComplete="off" />
      </div>

      {status.kind === "error" && (
        <p className="form-error" role="alert">{status.message}</p>
      )}
      <button className="btn" type="submit" disabled={sending}>
        {sending ? "Saving…" : "Join the waitlist"}
      </button>
      <p className="form-note">No spam, never shared. Ask us to delete your details any time.</p>
    </form>
  );
}
