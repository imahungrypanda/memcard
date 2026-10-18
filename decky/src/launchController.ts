// The launch/exit state machine, with every outside dependency injected so it can
// be unit-tested without Steam (see decky/tests/launch.test.ts). launch.ts wires
// it to SteamClient, the backend and the UI.
//
// Steam gives plugins no way to pause a launch, so for a mapped game we cancel it,
// sync, then launch it again ourselves and let that one launch through. Rules:
//   - never intercept a game that's already running (Resume), nor our own relaunch
//   - a second Play press while we're syncing is swallowed, not a second sync
//   - every --before-launch gets exactly one --after-exit with the same launch id,
//     even if the relaunch never turns into a running game (watchdog)
//   - exits are matched to the session they belong to, so late or duplicate
//     reports from an earlier launch can't end the current one

import type { SyncData } from "./types";

export interface Deps {
  cancel(actionId: number): void;
  run(gameId: string, launchSource: number): void;
  now(): number;
  setTimer(fn: () => void, ms: number): unknown;
  clearTimer(handle: unknown): void;
  autoSync(): boolean;
  isMapped(appid: number): boolean;
  beforeLaunch(appid: number, launchId: string): Promise<void>;
  afterExit(appid: number, launchId: string): Promise<void>;
  notify(message: string): void;
  newId(): string;
}

interface Session {
  id: string;
  started: boolean; // Steam reported it running after our relaunch
  instanceId?: number;
  watchdog?: unknown;
}

export const LET_THROUGH_MS = 10_000; // our RunGame must turn into a GameActionStart within this
export const START_TIMEOUT_MS = 90_000; // ...and a running game within this, or we close the session

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

export class LaunchController {
  private running = new Set<number>();
  private pending = new Set<number>();
  private letThrough = new Map<number, number>(); // appid -> expiry
  private sessions = new Map<number, Session>();
  private lastLaunched: number | null = null;
  private deps: Deps;

  constructor(deps: Deps) {
    this.deps = deps;
  }

  isRunning(appid?: number): boolean {
    return appid === undefined ? this.running.size > 0 : this.running.has(appid);
  }

  onGameActionStart(actionId: number, gameId: string, action: string, launchSource: number): void {
    if (action !== "LaunchApp") return;
    const appid = appidFromGameId(gameId);
    if (appid === null) return;
    const d = this.deps;

    const expiry = this.letThrough.get(appid);
    if (expiry !== undefined) {
      this.letThrough.delete(appid);
      if (d.now() <= expiry) return; // our own relaunch
    }
    if (this.running.has(appid)) return; // Resume: never sync under a running game
    if (this.pending.has(appid)) {
      d.cancel(actionId); // pressed Play again while we sync; the relaunch is coming
      d.notify("Still syncing saves…");
      return;
    }
    if (!d.autoSync() || !d.isMapped(appid)) return;

    d.cancel(actionId); // must happen synchronously, or Steam carries on
    this.pending.add(appid);
    this.lastLaunched = appid;
    this.endSession(appid); // a previous session that never reported its exit
    const session: Session = { id: d.newId(), started: false };
    this.sessions.set(appid, session);

    d.beforeLaunch(appid, session.id)
      .catch((e) => d.notify(`Launching with local saves: ${String(e)}`))
      .finally(() => {
        this.pending.delete(appid);
        this.letThrough.set(appid, d.now() + LET_THROUGH_MS);
        session.watchdog = d.setTimer(() => {
          // The relaunch never became a running game (refused, a dialog backed out of…).
          if (this.sessions.get(appid) === session && !session.started) this.endSession(appid);
        }, START_TIMEOUT_MS);
        d.run(gameId, launchSource);
      });
  }

  onLifetime(unAppID: number, instanceId: number, bRunning: boolean): void {
    // Some Steam builds report 0 for non-Steam shortcuts; fall back to what we launched.
    const appid = (unAppID >>> 0) || this.lastLaunched;
    if (!appid) return;
    const session = this.sessions.get(appid);
    if (bRunning) {
      this.running.add(appid);
      if (session && !session.started) {
        session.started = true;
        session.instanceId = instanceId;
        if (session.watchdog !== undefined) this.deps.clearTimer(session.watchdog);
      }
      return;
    }
    this.running.delete(appid);
    if (this.lastLaunched === appid) this.lastLaunched = null;
    // Only end the session this exit belongs to: a late or duplicate exit from an
    // earlier launch arrives before the new one is running, or with another instance.
    if (session && session.started && (session.instanceId === undefined || session.instanceId === instanceId)) {
      this.endSession(appid);
    }
  }

  /** Send the one --after-exit for this app's session, if there is one. */
  private endSession(appid: number): void {
    const session = this.sessions.get(appid);
    if (!session) return;
    this.sessions.delete(appid);
    if (session.watchdog !== undefined) this.deps.clearTimer(session.watchdog);
    this.deps.afterExit(appid, session.id).catch((e) => this.deps.notify(`Saves not uploaded yet: ${String(e)}`));
  }
}

/** One-line summary of a sync result for a toast. */
export function describe(data: SyncData | null | undefined): { text: string; conflicts: number; failed: number } {
  const r = data?.results ?? [];
  const up = r.reduce((n, g) => n + g.pushed.length, 0);
  const down = r.reduce((n, g) => n + g.pulled.length, 0);
  const conflicts = r.reduce((n, g) => n + g.conflicts.length, 0);
  const failed = r.filter((g) => g.status === "error" || g.status === "skipped").length;
  const parts = [up && `${up} up`, down && `${down} down`].filter(Boolean);
  return { text: parts.length ? parts.join(", ") : "Up to date", conflicts, failed };
}
