import { toaster } from "@decky/api";
import { askResolutions } from "./conflicts";
import { cachedGroups, currentSettings, errorText, isMapped, memcard, refreshGroups } from "./backend";
import type { SyncData } from "./types";

// Steam gives plugins no way to pause a launch, so: cancel it, sync, then launch
// it again ourselves, letting that second launch through once. Shortcuts are
// never edited, so Steam ROM Manager can't undo this.

const letThrough = new Set<number>();
const exiting = new Set<number>();
// One id per launch, passed to both --before-launch and --after-exit, so a late
// exit report from an earlier launch can't act on the game that's running now.
const launchIds = new Map<number, string>();
let lastLaunched: number | null = null;

function newLaunchId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

/** Steam's gameId for a non-Steam shortcut is (appid << 32) | 0x02000000; for Steam games it's the appid. */
export function appidFromGameId(gameId: string): number | null {
  try {
    const n = BigInt(gameId);
    const appid = n > 0xffffffffn ? n >> 32n : n;
    return appid > 0n ? Number(appid) : null;
  } catch {
    return null;
  }
}

function toast(title: string, body: string) {
  toaster.toast({ title, body, duration: 4000 });
}

function summarize(data: SyncData | null): string {
  const r = data?.results ?? [];
  const up = r.reduce((n, g) => n + g.pushed.length, 0);
  const down = r.reduce((n, g) => n + g.pulled.length, 0);
  const parts = [up && `${up} up`, down && `${down} down`].filter(Boolean);
  return parts.length ? parts.join(", ") : "Up to date";
}

async function beforeLaunch(appid: number): Promise<void> {
  const settings = currentSettings();
  toast("Memcard", "Syncing saves before launch…");
  const id = newLaunchId();
  launchIds.set(appid, id);
  const first = await memcard<SyncData>(["sync", "--appid", appid, "--before-launch", "--launch-id", id]);
  if (first.error) {
    toast("Memcard: launching with local saves", errorText(first.error));
    return;
  }
  for (const w of first.warnings) toast("Memcard", w);
  const results = first.result?.results ?? [];
  const failed = results.filter((r) => r.error && r.status !== "conflicts");
  if (failed.length) toast("Memcard: some saves didn't sync", failed.map((r) => `${r.group}: ${errorText(r.error)}`).join("\n"));

  if (results.some((r) => r.conflicts.length)) {
    const me = cachedGroups()?.machine.name ?? "this Deck";
    const resolve = await askResolutions(results, me);
    if (resolve.length) {
      const second = await memcard<SyncData>(["sync", "--appid", appid, ...resolve]);
      if (second.error) toast("Memcard", errorText(second.error));
      else if (settings.notify) toast("Memcard", `Conflicts settled. ${summarize(second.result)}`);
    } else {
      toast("Memcard", "Conflicts left for later; those saves weren't touched.");
    }
  } else if (settings.notify) {
    toast("Memcard", summarize(first.result));
  }
}

async function afterExit(appid: number): Promise<void> {
  if (exiting.has(appid)) return; // Steam can report an exit twice
  exiting.add(appid);
  try {
    const settings = currentSettings();
    const id = launchIds.get(appid);
    const r = await memcard<SyncData & { stale?: boolean }>(
      ["sync", "--appid", appid, "--after-exit", ...(id ? ["--launch-id", id] : [])],
    );
    if (r.result?.stale) return;
    if (launchIds.get(appid) === id) launchIds.delete(appid);
    if (r.error) toast("Memcard: saves not uploaded yet", errorText(r.error));
    else if (r.result?.results.some((g) => g.conflicts.length))
      toast("Memcard: save conflict", "Open Memcard in the Quick Access menu to decide.");
    else if (settings.notify) toast("Memcard", `Saves synced. ${summarize(r.result)}`);
    refreshGroups();
  } finally {
    exiting.delete(appid);
  }
}

export function registerLaunchHooks(): () => void {
  const start = SteamClient.Apps.RegisterForGameActionStart((actionId, gameId, action, launchSource) => {
    if (action !== "LaunchApp") return;
    const appid = appidFromGameId(gameId);
    if (appid === null) return;
    if (letThrough.delete(appid)) {
      lastLaunched = appid;
      return;
    }
    lastLaunched = appid;
    if (!currentSettings().auto_sync || !isMapped(appid)) return;
    // Decide synchronously: Steam proceeds unless the action is cancelled now.
    SteamClient.Apps.CancelGameAction(actionId);
    (async () => {
      try {
        await beforeLaunch(appid);
      } catch (e) {
        toast("Memcard: launching with local saves", String(e));
      } finally {
        letThrough.add(appid);
        SteamClient.Apps.RunGame(gameId, "", -1, launchSource);
      }
    })();
  });

  const lifetime = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n) => {
    if (n.bRunning) return;
    // Some Steam builds report 0 for non-Steam shortcuts; fall back to what we launched.
    const appid = n.unAppID || lastLaunched;
    if (!appid || !isMapped(appid)) return;
    if (appid === lastLaunched) lastLaunched = null;
    if (currentSettings().auto_sync) afterExit(appid);
  });

  return () => {
    start.unregister();
    lifetime.unregister();
  };
}
