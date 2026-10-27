// Injects server-rendered HTML into the built pages so they paint before JS loads.
import { readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const { render } = await import(pathToFileURL(`${root}dist-ssr/entry-server.js`).href);

for (const [file, page] of [["dist/index.html", "landing"], ["dist/privacy/index.html", "privacy"]]) {
  const path = `${root}${file}`;
  const html = await readFile(path, "utf8");
  if (!html.includes("<!--app-->")) throw new Error(`${file} has no <!--app--> marker`);
  await writeFile(path, html.replace("<!--app-->", render(page)));
  console.log(`prerendered ${file}`);
}
await rm(`${root}dist-ssr`, { recursive: true, force: true });
