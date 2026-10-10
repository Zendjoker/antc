import type { LinkState } from "@/lib/api/queries";

/** The orb shows the assistant's real state; connection problems override it. */
export function orbState(link: LinkState, state?: string): string {
  if (link === "disconnected") return "disconnected";
  if (link === "offline") return "offline";
  if (link === "connecting") return "connecting";
  return state ?? "quiet";
}
