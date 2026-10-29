import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import AdminApp from "./Admin";
import "./styles.css";

const standalone =
  matchMedia("(display-mode: standalone)").matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true;

if (standalone) {
  // iOS black-translucent bug: viewport = screen − status bar, so the bottom strip is unpaintable and the home-indicator padding is dead space.
  const root = document.documentElement;
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;top:0;left:0;width:0;height:env(safe-area-inset-top);visibility:hidden;pointer-events:none";
  document.documentElement.appendChild(probe);
  const fit = () => {
    const portrait = innerHeight >= innerWidth;
    const screenH = portrait
      ? Math.max(screen.width, screen.height)
      : Math.min(screen.width, screen.height);
    root.style.setProperty("--app-h", `${innerHeight}px`);
    const topInset = probe.offsetHeight;
    root.classList.toggle("short-view", topInset > 0 && screenH - innerHeight > 20);
  };
  root.classList.add("standalone");
  fit();
  addEventListener("resize", fit);
  addEventListener("orientationchange", fit);
}

const Root = window.location.pathname === "/admin" ? AdminApp : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
