import "./styles.css";
import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { Landing } from "./Landing";
import { Privacy } from "./Privacy";

const root = document.getElementById("root")!;
const page = root.dataset.page === "privacy" ? <Privacy /> : <Landing />;
hydrateRoot(root, <StrictMode>{page}</StrictMode>);
