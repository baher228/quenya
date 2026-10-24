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

export function Provenance({ source, condition }: { source: string; condition?: string | null }) {
  const name = source === "vinted" ? "Vinted" : source === "retail" ? "Retail" : source;
  return (
    <span className="prov">
      {name}
      {condition ? ` · ${condition}` : ""}
    </span>
  );
}

export function pretty(k: string) {
  const value = k
    .replace(/^(colou?rs?|aesthetics?|styles?|categor(?:y|ies)|materials?|patterns?|fits?|eras?|brands?|slots?)[_:]/i, "")
    .replace(/_/g, " ");
  return value === value.toLowerCase()
    ? value.replace(/\b[a-z]/g, letter => letter.toUpperCase())
    : value;
}

export function uniqueTasteEntries(
  entries: readonly (readonly [string, number])[],
  limit?: number,
) {
  const seen = new Set<string>();
  const unique = entries.filter(([key, value]) => {
    const sign = value > 0 ? "+" : "−";
    const label = pretty(key).toLocaleLowerCase("en-GB");
    const identity = `${sign}:${label}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
  return limit === undefined ? unique : unique.slice(0, limit);
}

export function gbpText(s: string) {
  return s.replace(/(\d[\d,]*(?:\.\d+)?)\s*GBP/g, (_match, amount: string) =>
    `£${Number(amount.replace(/,/g, "")).toLocaleString("en-GB")}`,
  );
}
