// Dev-only entry point: one origin (http://127.0.0.1:3000) in front of `next dev` and the Python dashboard server.
//
// Why not a Next.js rewrite: a rewrite replaces the Host header with the backend's own, which silently disables the
// backend's DNS-rebinding guard. This proxy applies the same Host rule first, and additionally requires that any
// browser Origin on /api requests is this proxy's own origin. It never adds credentials or relaxes backend checks;
// the backend still enforces X-Jarvis, Origin and executor safety on every request.
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";

const PORT = Number(process.env.ZEND_DEV_PORT ?? 3000);
const NEXT_PORT = PORT + 100;
const BACKEND = new URL(process.env.ZEND_BACKEND_URL ?? "http://127.0.0.1:8765");
const LOCAL = new Set(["127.0.0.1", "localhost", "::1"]);

// Same rule as room_agent.control.local_host
function localHost(host) {
  let name = String(host ?? "").trim().toLowerCase();
  if (name.startsWith("[")) name = name.slice(1).split("]")[0];
  else if ((name.match(/:/g) ?? []).length === 1) name = name.split(":")[0];
  return LOCAL.has(name);
}

function deny(res, why) {
  res.writeHead(403, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: `refused: ${why}` }));
}

const server = http.createServer((req, res) => {
  if (!localHost(req.headers.host)) return deny(res, "not a local request");
  const isApi = req.url.startsWith("/api/");
  if (isApi) {
    const origin = req.headers.origin;
    if (origin && origin !== `http://${req.headers.host}`) return deny(res, "cross-origin request");
  }
  const target = isApi ? { hostname: BACKEND.hostname, port: BACKEND.port } : { hostname: "127.0.0.1", port: NEXT_PORT };
  const headers = { ...req.headers, host: `${target.hostname}:${target.port}` };
  const up = http.request({ ...target, path: req.url, method: req.method, headers }, (r) => {
    res.writeHead(r.statusCode ?? 502, r.headers);
    r.pipe(res);
  });
  up.on("error", () => { if (!res.headersSent) res.writeHead(502); res.end(); });
  req.pipe(up);
});

// Next's hot-reload socket
server.on("upgrade", (req, socket, head) => {
  if (!localHost(req.headers.host) || req.url.startsWith("/api/")) return socket.destroy();
  const up = net.connect(NEXT_PORT, "127.0.0.1", () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` +
      Object.entries({ ...req.headers, host: `127.0.0.1:${NEXT_PORT}` }).map(([k, v]) => `${k}: ${v}`).join("\r\n") + "\r\n\r\n");
    up.write(head);
    socket.pipe(up).pipe(socket);
  });
  up.on("error", () => socket.destroy());
  socket.on("error", () => up.destroy());
});

const next = spawn(process.execPath, ["node_modules/next/dist/bin/next", "dev", "-p", String(NEXT_PORT), "-H", "127.0.0.1"], { stdio: "inherit" });
next.on("exit", (code) => process.exit(code ?? 0));
process.on("SIGINT", () => next.kill());

server.listen(PORT, "127.0.0.1", () => console.log(`ZendAgent dev: http://127.0.0.1:${PORT}  (api -> ${BACKEND.origin})`));
