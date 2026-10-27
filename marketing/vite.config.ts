import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vite";

const api = process.env.QUENYA_API ?? "http://127.0.0.1:8000";
// Canonical origin for OG/canonical URLs. Change when the final domain is chosen.
const site = (process.env.SITE_URL ?? "https://quenya-marketing.vercel.app").replace(/\/$/, "");

const siteUrl = {
  name: "site-url",
  transformIndexHtml: {
    order: "pre" as const,
    handler: (html: string) => html.replaceAll("__SITE_URL__", site),
  },
};

export default defineConfig({
  plugins: [react(), siteUrl],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        privacy: resolve(__dirname, "privacy/index.html"),
      },
    },
  },
  server: {
    port: 5174,
    proxy: { "/api": api },
  },
  preview: {
    port: 4174,
    proxy: { "/api": api },
  },
});
