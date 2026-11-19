import { callable } from "@decky/api";
import type { GroupsData, RunResult } from "./types";

const memcardCall = callable<[args: string[], stdin: string | null, runId: string | null], RunResult>("memcard");
const getSettingsCall = callable<[], Settings>("get_settings");
const setSettingCall = callable<[key: keyof Settings, value: boolean], Settings>("set_setting");
export const readTokenFile = callable<[path: string], string>("read_token_file");
export const environment = callable<[], { memcard_bundled: boolean; configured: boolean; version: string | null }>("environment");

export interface Settings {
  auto_sync: boolean;
  notify: boolean;
  check_updates: boolean;
}

// Cached so the launch hook can decide synchronously whether to intercept.
let settings: Settings = { auto_sync: true, notify: true, check_updates: true };

export async function loadSettings(): Promise<Settings> {
  settings = await getSettingsCall();
  return settings;
}

export function currentSettings(): Settings {
  return settings;
}

export async function updateSetting(key: keyof Settings, value: boolean): Promise<Settings> {
  settings = await setSettingCall(key, value);
  return settings;
}

export function memcard<T = any>(args: (string | number)[], opts: { stdin?: unknown; runId?: string } = {}): Promise<RunResult<T>> {
  const stdin = opts.stdin === undefined ? null : JSON.stringify(opts.stdin);
  return memcardCall(args.map(String), stdin, opts.runId ?? null) as Promise<RunResult<T>>;
}

// --- the group mapping, cached so the launch hook can decide synchronously -----

let groups: GroupsData | null = null;
const listeners = new Set<(g: GroupsData | null) => void>();

export async function refreshGroups(): Promise<GroupsData | null> {
  const r = await memcard<GroupsData>(["groups"]);
  // Keep the last good mapping on a transient failure: the launch hook relies on it.
  if (r.result || r.error?.code === "config") groups = r.result;
  listeners.forEach((l) => l(groups));
  return groups;
}

export function cachedGroups(): GroupsData | null {
  return groups;
}

export function onGroups(l: (g: GroupsData | null) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function isMapped(appid: number): boolean {
  return !!groups?.groups.some((g) => g.appids.includes(appid));
}
