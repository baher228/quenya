import "@fontsource-variable/archivo/wdth.css";
import "@fontsource-variable/archivo/wght.css";
import "@fontsource-variable/bodoni-moda/wght-italic.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./styles.css";
import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { Landing } from "./Landing";
import { Privacy } from "./Privacy";

const root = document.getElementById("root")!;
const page = root.dataset.page === "privacy" ? <Privacy /> : <Landing />;
hydrateRoot(root, <StrictMode>{page}</StrictMode>);
