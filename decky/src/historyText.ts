// What the Cloud versions screens say. Pure (no @decky imports) so Node can test it.
//
// Same rules as the conflict prompt: say "this machine" and "the cloud", never "Deck",
// and every button says what it does. A restore reaches every machine, so the confirm
// screen spells out what goes back, what goes to the trash, and that it can be undone.

import type { HistoryData, HistorySide, HistoryVersion, UnitKind } from "./types";

export interface Me {
  id: string;
  name: string;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** "Today, 14:02", "Yesterday, 21:40", "Tue 3 Nov, 09:15", or "3 Nov 2025, 09:15" (local time). */
export function when(ts: string | null | undefined, now: Date = new Date()): string {
  if (!ts) return "an unknown time";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days === 0) return `Today, ${time}`;
  if (days === 1) return `Yesterday, ${time}`;
  if (d.getFullYear() === now.getFullYear()) return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}, ${time}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${time}`;
}

/** "this machine" for our own id, otherwise the machine's name. */
export function who(id: string | null | undefined, machines: Record<string, string>, me: Me): string {
  if (id && id === me.id) return "this machine";
  return (id && machines[id]) || "another machine";
}

/** A save's display name: the file or folder name without its extension. "" (whole group) -> the group's name. */
export function saveName(unit: string, groupLabel: string): string {
  if (!unit) return groupLabel;
  const base = unit.split("/").pop() || unit;
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

export function isMemoryCard(unit: string): boolean {
  return /\.(ps2|mcd|mcr|mc|raw)$/i.test(unit);
}

export function versionTitle(v: HistoryVersion, data: HistoryData, me: Me, now?: Date): string {
  return `${when(v.committed_at, now)} · ${who(v.committed_by, data.machines, me)}`;
}

function names(list: string[], max = 3): string {
  if (list.length <= max) return list.join(", ");
  return `${list.slice(0, max).join(", ")} and ${list.length - max} more`;
}

/** One line under a version: what it changed. */
export function changeSummary(v: HistoryVersion, kind: UnitKind, groupLabel: string): string {
  if (v.changes === null) return "What it changed isn't known: the version before it was cleaned up.";
  if (!v.changes.length) return "No changes to saves.";
  if (kind === "group") {
    const n = v.changes.length;
    return `${n} file${n === 1 ? "" : "s"} changed: ${names(v.changes.map((c) => saveName(c.path, groupLabel)))}`;
  }
  const byUnit = new Map<string, string>();
  for (const c of v.changes) {
    const prev = byUnit.get(c.unit);
    byUnit.set(c.unit, prev && prev !== c.change ? "modified" : c.change);
  }
  const verb = { added: "first saved", modified: "changed", deleted: "deleted" } as const;
  return names([...byUnit].map(([u, c]) => `${saveName(u, groupLabel)} ${verb[c as keyof typeof verb]}`));
}

/** "In this version: Yesterday, 21:40 · Steam Deck → Now: Today, 09:15 · this machine" */
export function thenNow(then: HistorySide | null, now: HistorySide | null, me: Me, machines: Record<string, string>, at?: Date): string {
  const side = (s: HistorySide | null) => (s ? `${when(s.written_at, at)} · ${who(s.written_by, machines, me)}` : "not there");
  return `In this version: ${side(then)} → Now: ${now ? side(now) : "deleted"}`;
}

/** Which saves start switched on: the ones this version changed (or the filtered one), never all of them. */
export function initialSelection(v: HistoryVersion, filter: string | null): string[] {
  const restorable = new Set(v.differs.filter((d) => d.restorable).map((d) => d.unit));
  if (filter) return restorable.has(filter) ? [filter] : [];
  const changed = new Set((v.changes ?? []).map((c) => c.unit));
  return [...restorable].filter((u) => changed.has(u));
}

/** Files a restore of `units` removes because they didn't exist in that version (dir and group units). */
export function removedFiles(v: HistoryVersion, units: string[]): string[] {
  const chosen = new Set(units);
  return v.differs
    .filter((d) => chosen.has(d.unit))
    .flatMap((d) => d.files.filter((f) => !f.then && f.now).map((f) => f.path));
}

export interface Confirm {
  title: string;
  body: string;
  notes: string[];
  button: string;
}

export function confirmText(
  v: HistoryVersion,
  data: HistoryData,
  groupLabel: string,
  units: string[],
  me: Me,
  now?: Date,
): Confirm {
  const whole = data.unit === "group";
  const one = !whole && units.length === 1;
  const from = `${when(v.committed_at, now)} (${who(v.committed_by, data.machines, me)})`;
  const what = whole ? `All ${groupLabel} saves go` : one ? `${saveName(units[0], groupLabel)} goes` : `${units.length} saves go`;
  const notes: string[] = [];
  for (const u of whole ? [] : units) {
    if (isMemoryCard(u)) notes.push(`${saveName(u, groupLabel)} is a memory card. Every game saved on it goes back to that version.`);
  }
  const removed = removedFiles(v, whole ? v.differs.map((d) => d.unit) : units);
  if (removed.length) {
    notes.push(`Not saved yet in that version, so removed here: ${names(removed.map((p) => saveName(p, groupLabel)), 5)}. They go to Memcard's trash.`);
  }
  return {
    title: whole ? `Restore ${groupLabel} saves?` : one ? `Restore ${saveName(units[0], groupLabel)}?` : `Restore ${units.length} saves?`,
    body:
      `${what} back to the version from ${from}. ` +
      `This machine's current ${one ? "copy goes" : "copies go"} to Memcard's trash first. ` +
      `Your other machines get the restored ${one ? "save" : "saves"} on their next sync. ` +
      "The version you're replacing stays in Cloud versions, so you can undo this.",
    notes,
    button: whole ? `Restore ${groupLabel} saves` : `Restore ${units.length} save${units.length === 1 ? "" : "s"}`,
  };
}

/** Arguments for `memcard history restore`; every chosen save is named, so the UI never rolls back a whole group by accident. */
export function restoreArgs(group: string, generation: number, kind: UnitKind, units: string[], resolve: string[] = []): string[] {
  const picks = kind === "group" ? [] : units.flatMap((u) => ["--unit", u]);
  return ["history", "restore", group, String(generation), ...picks, ...resolve];
}

/** Why Restore is greyed out, or null. Browsing stays available either way. */
export function blockedReason(playing: boolean, pendingConflict: boolean, groupLabel: string): string | null {
  if (playing) return "You can restore after you quit the game.";
  if (pendingConflict) return `${groupLabel} has a conflict waiting. Resolve it in the Memcard panel first, then restore.`;
  return null;
}

export function restoredToast(groupLabel: string, kind: UnitKind, units: string[], v: HistoryVersion, now?: Date): string {
  const what = kind === "group" ? `${groupLabel} saves` : units.length === 1 ? saveName(units[0], groupLabel) : `${units.length} saves`;
  return `Restored ${what} from ${when(v.committed_at, now)}. Your other machines get it on their next sync.`;
}
