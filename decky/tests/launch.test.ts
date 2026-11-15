// Launch/exit state machine, against a fake Steam. Run: node --test decky/tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import { appidFromGameId, describe, LaunchController, LET_THROUGH_MS, START_TIMEOUT_MS } from "../src/launchController.ts";

const SHORTCUT = 3141592653;
const GAME_ID = ((BigInt(SHORTCUT) << 32n) | 0x02000000n).toString();

function world(opts: { autoSync?: boolean; mapped?: number[] } = {}) {
  const log: string[] = [];
  let clock = 0;
  let ids = 0;
  const timers: { at: number; fn: () => void; dead: boolean }[] = [];
  let release: (() => void)[] = [];
  const state = { autoSync: opts.autoSync ?? true };
  const c = new LaunchController({
    cancel: (id) => log.push(`cancel ${id}`),
    run: () => log.push("run"),
    now: () => clock,
    setTimer: (fn, ms) => {
      const t = { at: clock + ms, fn, dead: false };
      timers.push(t);
      return t;
    },
    clearTimer: (t) => ((t as { dead: boolean }).dead = true),
    autoSync: () => state.autoSync,
    isMapped: (a) => (opts.mapped ?? [SHORTCUT]).includes(a),
    beforeLaunch: (a, id) => {
      log.push(`before ${a} ${id}`);
      return new Promise<void>((r) => release.push(r)); // finishes when the test says so
    },
    afterExit: async (a, id) => void log.push(`after ${a} ${id ?? "-"}`),
    notify: (m) => log.push(`notify ${m}`),
    newId: () => `L${++ids}`,
  });
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return {
    c,
    log,
    state,
    async finishSync() {
      release.forEach((r) => r());
      release = [];
      await flush();
      await flush();
    },
    advance(ms: number) {
      clock += ms;
      for (const t of timers) if (!t.dead && t.at <= clock) (t.dead = true), t.fn();
    },
    press: (action = 1) => c.onGameActionStart(action, GAME_ID, "LaunchApp", 100),
    started: (instance = 7, appid = SHORTCUT) => c.onLifetime(appid, instance, true),
    exited: (instance = 7, appid = SHORTCUT) => c.onLifetime(appid, instance, false),
  };
}

test("decodes shortcut and Steam game ids", () => {
  assert.equal(appidFromGameId(GAME_ID), SHORTCUT);
  assert.equal(appidFromGameId("1145360"), 1145360);
  assert.equal(appidFromGameId("nonsense"), null);
  assert.equal(appidFromGameId("0"), null);
});

test("happy path: cancel, sync, relaunch once, after-exit with the same id", async () => {
  const w = world();
  w.press(1);
  assert.deepEqual(w.log, ["cancel 1", `before ${SHORTCUT} L1`]);
  await w.finishSync();
  assert.deepEqual(w.log.slice(2), ["run"]);
  w.press(2); // our relaunch arrives as a new GameActionStart
  assert.ok(!w.log.includes("cancel 2"), "our own relaunch must go through");
  w.started();
  w.exited();
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L1`);
  w.exited(); // duplicate exit report
  assert.equal(w.log.filter((l) => l.startsWith("after")).length, 1);
});

test("Resume on a running game is never intercepted", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2);
  w.started();
  const before = w.log.length;
  w.press(3); // Play/Resume while it runs
  assert.equal(w.log.length, before, "no cancel, no sync under a running game");
});

test("a second press while syncing is swallowed, not a second sync", async () => {
  const w = world();
  w.press(1);
  w.press(2);
  assert.deepEqual(w.log, ["cancel 1", `before ${SHORTCUT} L1`, "cancel 2", "notify Still syncing saves…"]);
  await w.finishSync();
  assert.equal(w.log.filter((l) => l === "run").length, 1);
});

test("let-through expires: a later Play still syncs first", async () => {
  const w = world();
  w.press(1);
  await w.finishSync(); // RunGame fires, but Steam never reports a GameActionStart for it
  w.advance(LET_THROUGH_MS + 1);
  w.press(2);
  assert.ok(w.log.includes("cancel 2") && w.log.filter((l) => l.startsWith("before")).length === 2);
});

test("a relaunch that never starts still gets its after-exit (watchdog)", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.advance(START_TIMEOUT_MS + 1);
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L1`);
});

test("the watchdog doesn't fire once the game is running", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2);
  w.started();
  w.advance(START_TIMEOUT_MS + 1);
  assert.ok(!w.log.some((l) => l.startsWith("after")));
});

test("turning auto-sync off mid-game still closes the session", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2);
  w.started();
  w.state.autoSync = false;
  w.exited();
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L1`);
});

test("Steam reporting appid 0 falls back to the launched game", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2);
  w.started(7, 0);
  w.exited(7, 0);
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L1`);
});

test("signed appids from Steam are normalised", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2);
  w.started(7, SHORTCUT | 0); // the same 32 bits, read as a negative int32
  w.exited(7, SHORTCUT | 0);
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L1`);
});

test("a late exit from launch 1 can't end launch 2", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2);
  w.started(7);
  w.exited(7); // launch 1 ends: after L1
  w.press(3);
  await w.finishSync(); // launch 2 relaunching, not yet running
  w.exited(7); // late duplicate from launch 1
  assert.equal(w.log.filter((l) => l.startsWith("after")).length, 1, "ignored before launch 2 runs");
  w.press(4);
  w.started(8);
  w.exited(7); // another stale report, wrong instance
  assert.equal(w.log.filter((l) => l.startsWith("after")).length, 1, "ignored: different instance");
  w.exited(8);
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L2`);
});

test("unmapped games and auto-sync off launch untouched", () => {
  const off = world({ autoSync: false });
  off.press(1);
  const unmapped = world({ mapped: [] });
  unmapped.press(1);
  assert.deepEqual(off.log, []);
  assert.deepEqual(unmapped.log, []);
});

test("other actions are ignored", () => {
  const w = world();
  w.c.onGameActionStart(1, GAME_ID, "Install", 100);
  assert.deepEqual(w.log, []);
});


test("after a reload mid-game, Resume isn't intercepted", () => {
  const w = world();
  w.c.seedRunning([SHORTCUT]); // fresh controller learns what's running from Steam
  w.press(1);
  assert.deepEqual(w.log, []);
});

test("after a reload mid-game, quitting still pushes and clears the marker", () => {
  const w = world();
  w.c.seedRunning([SHORTCUT]);
  w.exited(7);
  assert.deepEqual(w.log, [`after ${SHORTCUT} -`]);
});

test("a game that starts after the watchdog still gets an after-exit when it quits", async () => {
  const w = world();
  w.press(1);
  await w.finishSync();
  w.press(2); // our relaunch, let through
  w.advance(START_TIMEOUT_MS + 1); // ...but Steam takes >90 s to report it running; watchdog closes L1
  assert.equal(w.log.at(-1), `after ${SHORTCUT} L1`);
  w.started(9);
  w.exited(9);
  assert.equal(w.log.at(-1), `after ${SHORTCUT} -`, "its exit still pushes and clears the marker");
});

test("no session and auto-sync off: an exit does nothing", () => {
  const w = world({ autoSync: false });
  w.started(7);
  w.exited(7);
  assert.deepEqual(w.log, []);
});

test("the panel hears about games starting and stopping", () => {
  const w = world();
  const seen: boolean[] = [];
  w.c.onChange(() => seen.push(w.c.isRunning()));
  w.started(7);
  w.exited(7);
  // onChange also fires for sync start/finish; the running flag must go true, then false.
  assert.deepEqual(seen.filter((v, i) => i === 0 || v !== seen[i - 1]), [true, false]);
});

test("describe() spells out what moved", () => {
  const g = (pushed: string[], pulled: string[]) => ({ group: "g", status: "ok", pushed, pulled, conflicts: [] });
  const text = (...r: unknown[]) => describe({ results: r } as never).text;
  assert.equal(text(g(["a"], [])), "1 save file uploaded");
  assert.equal(text(g(["a", "b"], ["c"])), "2 save files uploaded, 1 save file downloaded");
  assert.equal(text(g([], [])), "Already up to date");
});

test("isBusy covers the whole launch: before-launch sync, the game, and the after-exit sync", async () => {
  const w = world();
  assert.equal(w.c.isBusy(), false);
  w.c.onGameActionStart(1, GAME_ID, "LaunchApp", 0);
  assert.equal(w.c.isBusy(), true, "before-launch sync in flight");
  await w.finishSync();
  assert.equal(w.c.isBusy(), true, "relaunch pending");
  w.c.onLifetime(SHORTCUT, 7, true);
  assert.equal(w.c.isBusy(), true, "game running");
  w.c.onLifetime(SHORTCUT, 7, false);
  assert.equal(w.c.isBusy(), true, "after-exit sync in flight");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(w.c.isBusy(), false);
});

test("a lost exit report doesn't block updates once the game is no longer running", async () => {
  const w = world();
  w.c.onGameActionStart(1, GAME_ID, "LaunchApp", 0);
  await w.finishSync();
  w.c.onLifetime(SHORTCUT, 7, true);
  w.c.onLifetime(SHORTCUT, 8, false); // exit reported with another instance id: session stays open
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(w.c.isRunning(), false);
  assert.equal(w.c.isBusy(), false);
});
