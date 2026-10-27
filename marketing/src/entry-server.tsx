import { StrictMode } from "react";
import { renderToString } from "react-dom/server";
import { Landing } from "./Landing";
import { Privacy } from "./Privacy";

export function render(page: "landing" | "privacy"): string {
  return renderToString(<StrictMode>{page === "privacy" ? <Privacy /> : <Landing />}</StrictMode>);
}
