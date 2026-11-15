// Build dist/: bundle the shared runtime script, copy the page and the
// custom-function metadata, and draw placeholder icons (solid squares).
import { build } from "esbuild";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
mkdirSync(join(dist, "assets"), { recursive: true });

await build({
  entryPoints: [join(root, "src", "shared.ts")],
  bundle: true,
  format: "iife",
  target: "es2020",
  outfile: join(dist, "shared.js"),
  sourcemap: true,
  logLevel: "info",
});
copyFileSync(join(root, "src", "shared.html"), join(dist, "shared.html"));
copyFileSync(join(root, "src", "functions", "functions.json"), join(dist, "functions.json"));

// A minimal PNG encoder for a solid-colour square, so the repo carries no binaries.
function crc32(buf) {
  let c, crc = 0xffffffff;
  for (const byte of buf) {
    c = (crc ^ byte) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function squarePng(size, [r, g, b]) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // truecolour
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 3).map((_, i) => [r, g, b][i % 3])]);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
for (const size of [16, 32, 64, 80]) {
  writeFileSync(join(dist, "assets", `icon-${size}.png`), squarePng(size, [42, 91, 215]));
}
console.log("dist/ ready: serve it over https at the address in manifest.xml");
