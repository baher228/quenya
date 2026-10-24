import type { ReactNode } from "react";
import { ApiError } from "./api";

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const ids = error instanceof ApiError ? error.requestIds : [];
  return (
    <div className="errbox" role="alert">
      <p>{error instanceof Error ? error.message : String(error)}</p>
      {ids.length > 0 && <p className="mono small muted">Mubit {ids.join(", ")}</p>}
      {onRetry && <button className="btn ghost sm" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Loading({ children }: { children: ReactNode }) {
  return (
    <div className="loading" aria-live="polite">
      <span className="dots"><i /><i /><i /></span>
      <span>{children}</span>
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange }: {
  value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="radiogroup">
      {options.map(([v, label]) => (
        <button key={v} role="radio" aria-checked={value === v} className={value === v ? "sel" : ""} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Provenance({ source, origin, condition }: { source: string; origin?: string; condition?: string | null }) {
  const name = source === "vinted" ? "Vinted" : source === "retail" ? "Retail" : source;
  return (
    <span className="prov">
      {name}
      {condition ? ` · ${condition}` : ""}
      {origin ? <em className={origin === "live" ? "live" : ""}>{origin === "live" ? "live" : "snapshot"}</em> : null}
    </span>
  );
}
