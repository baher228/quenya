import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const standalone =
  matchMedia("(display-mode: standalone)").matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true;

if (standalone) {
  // iOS home-screen apps report a viewport shorter than the screen by the status bar.
  const fit = () => {
    const portrait = innerHeight >= innerWidth;
    const screenH = portrait
      ? Math.max(screen.width, screen.height)
      : Math.min(screen.width, screen.height);
    document.documentElement.style.setProperty("--app-h", `${Math.max(innerHeight, screenH)}px`);
  };
  fit();
  addEventListener("resize", fit);
  addEventListener("orientationchange", fit);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
