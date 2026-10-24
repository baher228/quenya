import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const standalone =
  matchMedia("(display-mode: standalone)").matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true;

if (standalone) {
  // iOS home-screen apps can get a viewport that stops above the home indicator,
  // in which case the bottom safe-area padding would be wasted space.
  const root = document.documentElement;
  const fit = () => {
    const portrait = innerHeight >= innerWidth;
    const screenH = portrait
      ? Math.max(screen.width, screen.height)
      : Math.min(screen.width, screen.height);
    root.style.setProperty("--app-h", `${innerHeight}px`);
    root.classList.toggle("short-view", screenH - innerHeight > 20);
  };
  root.classList.add("standalone");
  fit();
  addEventListener("resize", fit);
  addEventListener("orientationchange", fit);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
