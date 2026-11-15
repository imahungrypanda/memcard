import { fetchNoCors, toaster } from "@decky/api";
import { currentSettings, environment } from "./backend";
import { controller } from "./launch";
import { availableUpdate, RELEASES_API, type Update } from "./updates";

// Checks GitHub for a newer Memcard on load and every 12 hours, toasts once per version, and
// installs through Decky's own installer (its confirm prompt + SHA-256 check). See updates.ts.
// Decky removes the old version before it checks the hash, so a failed update leaves no Memcard
// until it's reinstalled from the URL (settings and saves are untouched).

const FIRST_CHECK_MS = 60_000; // let Steam finish starting up first
const EVERY_MS = 12 * 60 * 60 * 1000;
const UPDATE = 2; // Decky's InstallType.UPDATE

export interface UpdateState {
  installed: string | null;
  update: Update | null;
  checked: Date | null;
  checking: boolean;
  error: string | null;
}

let state: UpdateState = { installed: null, update: null, checked: null, checking: false, error: null };
let inFlight: Promise<UpdateState> | null = null;
const STALE_MS = 15 * 60 * 1000;
let toasted: string | null = null;
const listeners = new Set<(s: UpdateState) => void>();

function set(patch: Partial<UpdateState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l(state));
}

export function updateState(): UpdateState {
  return state;
}

export function onUpdateState(l: (s: UpdateState) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** The installed version, for the panel footer; read once at load. */
export async function loadInstalledVersion(): Promise<void> {
  try {
    set({ installed: (await environment()).version || null });
  } catch {
    // backend not ready; checkForUpdate will try again
  }
}

/** Opening the panel refreshes a check older than 15 minutes, so the answer is usually already there. */
export function checkIfStale(): void {
  if (!state.checked || Date.now() - state.checked.getTime() > STALE_MS) checkForUpdate({ quiet: true });
}

/** One check at a time: a press while a background check runs waits for that one. */
export function checkForUpdate(opts: { quiet?: boolean } = {}): Promise<UpdateState> {
  inFlight ??= check(opts).finally(() => (inFlight = null));
  return inFlight;
}

async function check(opts: { quiet?: boolean }): Promise<UpdateState> {
  set({ checking: true });
  try {
    const installed = state.installed ?? ((await environment()).version || null);
    // fetchNoCors goes through Decky's backend, so Steam's browser CORS rules don't apply.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("GitHub didn't answer. Try again later.")), 10_000);
    });
    let release: unknown;
    try {
      const get = async () => {
        const res = await fetchNoCors(RELEASES_API, { headers: { Accept: "application/vnd.github+json" } });
        if (!res.ok) throw new Error(res.status === 403 ? "GitHub is rate-limiting update checks; try later." : `GitHub answered ${res.status}.`);
        return res.json();
      };
      release = await Promise.race([get(), timeout]); // the timeout covers the body too
    } finally {
      clearTimeout(timer);
    }
    set({ installed, update: availableUpdate(installed, release), checked: new Date(), checking: false, error: null });
  } catch (e) {
    set({ checked: new Date(), checking: false, error: e instanceof Error && e.message ? e.message : "Couldn't reach GitHub." });
  }
  const u = state.update;
  // Never toast over a game or a launch/exit sync; the next check (or the panel) will show it.
  if (!opts.quiet && u && toasted !== u.version && !controller.isBusy()) {
    toasted = u.version;
    toaster.toast({
      title: "Memcard update available",
      body: `Version ${u.version} is ready. Select to install.`,
      duration: 8000,
      onClick: () => installUpdate(u),
    });
  }
  return state;
}

/** Hand the zip to Decky's installer. Decky shows its own confirm prompt and checks the hash. */
export function installUpdate(u: Update): boolean {
  // Updating reloads the plugin: it would kill a launch/exit sync and drop the launch hook.
  if (controller.isBusy()) {
    toaster.toast({ title: "Memcard", body: "Update after you quit the game and its saves finish syncing.", duration: 6000 });
    return false;
  }
  const backend = (window as unknown as { DeckyBackend?: { call: (route: string, ...args: unknown[]) => Promise<unknown> } })
    .DeckyBackend;
  if (!backend?.call) {
    toaster.toast({ title: "Memcard", body: "Update from Decky ⚙ → Developer → Install plugin from URL.", duration: 8000 });
    return false;
  }
  backend.call("utilities/install_plugin", u.artifact, "Memcard", u.version, u.hash, UPDATE).catch(() => {
    toaster.toast({ title: "Memcard", body: "Decky couldn't start the update. Try Install plugin from URL.", duration: 8000 });
  });
  return true;
}

/** Background checks, if enabled. Returns a stop function for onDismount. */
export function startUpdateChecks(): () => void {
  const tick = () => {
    if (currentSettings().check_updates) checkForUpdate();
  };
  const first = setTimeout(tick, FIRST_CHECK_MS);
  const every = setInterval(tick, EVERY_MS);
  return () => {
    clearTimeout(first);
    clearInterval(every);
  };
}
