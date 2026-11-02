import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrandPicker } from "./BrandPicker";
import "./styles.css";

const MOCK = [
  "Arket",
  "COS",
  "Noah",
  "Our Legacy",
  "APC",
  "Studio Nicholson",
  "Margaret Howell",
  "Engineered Garments",
];

const originalFetch = window.fetch.bind(window);
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.includes("/api/brands")) {
    const parsed = new URL(url, window.location.origin);
    const q = (parsed.searchParams.get("q") || "").toLowerCase();
    const brands = MOCK.filter(brand => brand.toLowerCase().includes(q));
    return new Response(JSON.stringify({ brands }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return originalFetch(input, init);
};

function Preview() {
  const [brands, setBrands] = useState<string[]>(["COS"]);
  return (
    <div className="phone" style={{ margin: "24px auto", maxWidth: 390 }}>
      <section className="screen">
        <p className="eyebrow">New style</p>
        <h1 className="h1">Preferred brands</h1>
        <BrandPicker value={brands} onChange={setBrands} />
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Preview />
  </StrictMode>,
);
