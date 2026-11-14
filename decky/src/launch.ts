import { toaster } from "@decky/api";
import { Router } from "@decky/ui";
import { cachedGroups, currentSettings, errorText, isMapped, memcard, refreshGroups } from "./backend";
import { askResolutions } from "./conflicts";
import { describe, LaunchController } from "./launchController";
import type { SyncData } from "./types";

// Wires the launch state machine (launchController.ts) to Steam, the backend and the UI.

function toast(title: string, body: string) {
  toaster.toast({ title, body, duration: 4000 });
}

// The game's name as Steam shows it (works for non-Steam shortcuts too), for toast titles.
function gameTitle(appid: number): string {
  try {
    const name = window.appStore?.GetAppOverviewByAppID(appid)?.display_name?.trim();
    if (name) return `Memcard · ${name}`;
  } catch {
    // appStore not ready; fall back to the plain title
  }
  return "Memcard";
}

async function beforeLaunch(appid: number, launchId: string): Promise<void> {
  const notify = currentSettings().notify;
  const title = gameTitle(appid);
  toast(title, "Syncing saves before launch…");
  const hook = ["sync", "--appid", appid, "--before-launch", "--launch-id", launchId];
  const first = await memcard<SyncData>(hook);
  if (first.error) return toast(title, `Launching with local saves. ${errorText(first.error)}`);
  first.warnings.forEach((w) => toast(title, w));
  const results = first.result?.results ?? [];
  const failed = results.filter((r) => r.status === "error" || r.status === "skipped");
  if (failed.length) toast(`${title}: some saves didn't sync`, failed.map((r) => `${r.group}: ${errorText(r.error)}`).join("\n"));

  if (!results.some((r) => r.conflicts.length)) {
    if (notify) toast(title, describe(first.result).text);
    return;
  }
  const resolve = await askResolutions(results, cachedGroups()?.machine.name ?? "this Deck");
  if (!resolve.length) return toast(title, "Conflicts left for later; those saves weren't touched.");
  // Still the pre-launch sync (same launch id), so core allows it while the launch is marked.
  const second = await memcard<SyncData>([...hook, ...resolve]);
  if (second.error) return toast(title, errorText(second.error));
  const d = describe(second.result);
  if (d.conflicts) toast(title, `${d.text}. Some conflicts are left for later; those saves weren't touched.`);
  else if (notify) toast(title, `Conflicts settled. ${d.text}`);
}

async function afterExit(appid: number, launchId: string | null): Promise<void> {
  const id = launchId ? ["--launch-id", launchId] : [];
  const r = await memcard<SyncData & { stale?: boolean }>(["sync", "--appid", appid, "--after-exit", ...id]);
  refreshGroups();
  if (r.result?.stale) return;
  const title = gameTitle(appid);
  if (r.error?.code === "cancelled") return toast(title, errorText(r.error));
  if (r.error) return toast(title, `Saves not uploaded yet. ${errorText(r.error)}`);
  const d = describe(r.result);
  if (d.conflicts) toast(title, "Save conflict. Open Memcard in the Quick Access menu to decide.");
  else if (d.failed) toast(title, "Some saves didn't sync. See the log in the Memcard panel.");
  else if (currentSettings().notify) toast(title, `Saves synced: ${d.text}.`);
}

export const controller = new LaunchController({
  cancel: (id) => SteamClient.Apps.CancelGameAction(id),
  run: (gameId, source) => SteamClient.Apps.RunGame(gameId, "", -1, source),
  now: () => Date.now(),
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  autoSync: () => currentSettings().auto_sync,
  isMapped,
  beforeLaunch,
  afterExit,
  notify: (m) => toast("Memcard", m),
  newId: () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
});

export function registerLaunchHooks(): () => void {
  try {
    controller.seedRunning(Router.RunningApps.map((a) => Number(a.appid)).filter(Number.isFinite));
  } catch {
    // Router not ready; lifetime notifications will fill it in
  }
  const start = SteamClient.Apps.RegisterForGameActionStart((actionId, gameId, action, launchSource) =>
    controller.onGameActionStart(actionId, gameId, action, launchSource),
  );
  const lifetime = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n) =>
    controller.onLifetime(n.unAppID, n.nInstanceID, n.bRunning),
  );
  return () => {
    start.unregister();
    lifetime.unregister();
  };
}
