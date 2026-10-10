export function timeAgo(epochSeconds: number, now = Date.now()) {
  const s = Math.max(0, Math.round(now / 1000 - epochSeconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function clock(epochSeconds: number) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function usd(n: number | undefined | null) {
  return typeof n === "number" ? `$${n.toFixed(2)}` : "—";
}

export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
