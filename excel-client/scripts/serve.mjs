// Serve dist/ over https://localhost:3000 for sideloading, with no extra
// dependencies. Uses the development certificate that the Office add-in dev
// tools install (npx office-addin-dev-certs install), or the files named by
// AA_DEV_CERT / AA_DEV_KEY.
import { createServer } from "node:https";
import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const certDir = join(homedir(), ".office-addin-dev-certs");
const cert = process.env.AA_DEV_CERT ?? join(certDir, "localhost.crt");
const key = process.env.AA_DEV_KEY ?? join(certDir, "localhost.key");
const port = Number(process.env.PORT ?? 3000);

if (!existsSync(cert) || !existsSync(key)) {
  console.error(`No development certificate at ${cert}.\nRun: npx office-addin-dev-certs install`);
  process.exit(1);
}
if (!existsSync(join(root, "shared.html"))) {
  console.error("dist/ is empty. Run: npm run build");
  process.exit(1);
}

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".map": "application/json",
  ".png": "image/png",
};

createServer({ cert: readFileSync(cert), key: readFileSync(key) }, (req, res) => {
  const path = decodeURIComponent(new URL(req.url ?? "/", "https://localhost").pathname);
  const file = normalize(join(root, path === "/" ? "shared.html" : path));
  if (!file.startsWith(root + sep) || !existsSync(file) || !statSync(file).isFile()) {
    res.writeHead(404).end("not found");
    return;
  }
  res.writeHead(200, {
    "Content-Type": types[extname(file)] ?? "application/octet-stream",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(readFileSync(file));
}).listen(port, "127.0.0.1", () => console.log(`serving dist/ on https://localhost:${port}`));
