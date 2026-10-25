import { mkdir, readFile, rm, writeFile } from "node:fs/promises";

const outputDirectory = new URL("../static/dist/", import.meta.url);
await rm(outputDirectory, { recursive: true, force: true });
await mkdir(outputDirectory, { recursive: true });
const source = await readFile(new URL("./src/app.ts", import.meta.url), "utf8");
// The foundation module intentionally uses only syntax shared by TS and modern JS.
await writeFile(new URL("app.js", outputDirectory), source.replace(/: void/g, ""));
console.log("Built browser modules to static/dist");
