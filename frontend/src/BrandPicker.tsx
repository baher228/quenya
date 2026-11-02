import { useEffect, useMemo, useState } from "react";
import { api } from "./api";

type BrandPickerProps = {
  value: string[];
  onChange: (brands: string[]) => void;
  max?: number;
};

export function BrandPicker({ value, onChange, max = 12 }: BrandPickerProps) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const selected = useMemo(
    () => new Set(value.map(brand => brand.toLocaleLowerCase("en-GB"))),
    [value],
  );

  useEffect(() => {
    let cancelled = false;
    const handle = window.setTimeout(() => {
      setLoading(true);
      api
        .brands(query, 24)
        .then(brands => {
          if (!cancelled) {
            setSuggestions(
              brands.filter(brand => !selected.has(brand.toLocaleLowerCase("en-GB"))),
            );
          }
        })
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [query, selected]);

  const add = (brand: string) => {
    const clean = brand.trim();
    if (!clean || selected.has(clean.toLocaleLowerCase("en-GB")) || value.length >= max) {
      return;
    }
    onChange([...value, clean]);
    setQuery("");
    setOpen(false);
  };

  const remove = (brand: string) => {
    onChange(value.filter(entry => entry !== brand));
  };

  return (
    <div className="group brand-picker">
      <span className="lbl">Preferred brands <em>optional</em></span>
      <p className="small muted">Boost these in your feed and outfits. Skip if you want to stay open.</p>
      {value.length > 0 && (
        <div className="chips brand-chips">
          {value.map(brand => (
            <button
              key={brand}
              type="button"
              className="chip sel"
              onClick={() => remove(brand)}
              aria-label={`Remove ${brand}`}
            >
              {brand} ×
            </button>
          ))}
        </div>
      )}
      {value.length < max && (
        <div className="brand-typeahead">
          <input
            type="search"
            value={query}
            placeholder="Search catalogue brands"
            autoComplete="off"
            maxLength={64}
            onChange={event => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={event => {
              if (event.key === "Enter") {
                event.preventDefault();
                const first = suggestions[0];
                if (first) add(first);
              }
              if (event.key === "Escape") setOpen(false);
            }}
            aria-label="Search preferred brands"
          />
          {open && (query.trim() || suggestions.length > 0) && (
            <ul className="brand-suggestions" role="listbox">
              {loading && <li className="muted">Searching…</li>}
              {!loading && suggestions.length === 0 && (
                <li className="muted">No matching catalogue brands</li>
              )}
              {suggestions.slice(0, 8).map(brand => (
                <li key={brand}>
                  <button type="button" onClick={() => add(brand)}>{brand}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
