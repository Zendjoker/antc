import { Activity, Brain, Cable, Gauge, LayoutDashboard, ListChecks, MessageSquare, Settings, type LucideIcon } from "lucide-react";

export interface NavItem { href: string; label: string; icon: LucideIcon; group: "Workspace" | "Manage" }

export const NAV: NavItem[] = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { href: "/assistant", label: "Chat", icon: MessageSquare, group: "Workspace" },
  { href: "/memory", label: "Memory", icon: Brain, group: "Workspace" },
  { href: "/activity", label: "Activity", icon: Activity, group: "Workspace" },
  { href: "/missions", label: "Missions", icon: ListChecks, group: "Workspace" },
  { href: "/status", label: "Status", icon: Gauge, group: "Manage" },
  { href: "/connections", label: "Connections", icon: Cable, group: "Manage" },
  { href: "/settings", label: "Settings", icon: Settings, group: "Manage" },
];

/** Where the original dashboard lives. Same origin when served by Python; explicit in dev. */
export const CLASSIC_URL = process.env.NEXT_PUBLIC_CLASSIC_URL ?? "http://127.0.0.1:8765/";