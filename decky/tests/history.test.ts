import assert from "node:assert/strict";
import { test } from "node:test";
import {
  blockedReason,
  changeSummary,
  confirmText,
  initialSelection,
  restoreArgs,
  saveName,
  thenNow,
  versionTitle,
  when,
  who,
} from "../src/historyText.ts";

const me = { id: "m-self", name: "Steam Machine" };
const machines = { "m-self": "Steam Machine", "m-deck": "Steam Deck" };
const now = new Date(2026, 10, 18, 15, 0); // Wed 18 Nov 2026, local time
const at = (y: number, mo: number, d: number, h: number, mi: number) => new Date(y, mo, d, h, mi).toISOString();
const side = (ts: string, by: string) => ({ size: 8192, written_at: ts, written_by: by, machine: machines[by as keyof typeof machines] });

const ZELDA = "Legend of Zelda, The - A Link to the Past (USA).srm";
const METROID = "Super Metroid (Japan, USA).srm";

function version(over: object = {}) {
  return {
    generation: 45,
    committed_at: at(2026, 10, 17, 21, 40),
    committed_by: "m-deck",
    machine: "Steam Deck",
    restored_from: null,
    current: false,
    changes: [{ path: ZELDA, unit: ZELDA, change: "modified", size: 8192 }],
    differs: [
      { unit: ZELDA, restorable: true, files: [{ path: ZELDA, then: side(at(2026, 10, 17, 21, 40), "m-deck"), now: side(at(2026, 10, 17, 22, 41), "m-deck") }] },
      { unit: METROID, restorable: true, files: [{ path: METROID, then: side(at(2026, 10, 16, 19, 5), "m-self"), now: side(at(2026, 10, 18, 14, 2), "m-self") }] },
      { unit: "Chrono Trigger (USA).srm", restorable: false, files: [{ path: "Chrono Trigger (USA).srm", then: null, now: side(at(2026, 10, 18, 9, 15), "m-deck") }] },
    ],
    ...over,
  } as never;
}

const data = (unit = "file") => ({ group: "retroarch", unit, head: 48, machines, more: false, versions: [] }) as never;

test("dates read like a person would say them", () => {
  assert.equal(when(at(2026, 10, 18, 14, 2), now), "Today, 14:02");
  assert.equal(when(at(2026, 10, 17, 21, 40), now), "Yesterday, 21:40");
  assert.equal(when(at(2026, 10, 3, 9, 5), now), "Tue 3 Nov, 09:05");
  assert.equal(when(at(2025, 10, 3, 9, 5), now), "3 Nov 2025, 09:05");
  assert.equal(when(null, now), "an unknown time");
});

test("this machine is never called a Deck, and unknown ids are 'another machine'", () => {
  assert.equal(who("m-self", machines, me), "this machine");
  assert.equal(who("m-deck", machines, me), "Steam Deck");
  assert.equal(who("m-gone", machines, me), "another machine");
  assert.equal(versionTitle(version({ committed_by: "m-self" }), data(), me, now), "Yesterday, 21:40 · this machine");
});

test("save names drop the extension; a whole group uses the group's name", () => {
  assert.equal(saveName(ZELDA, "RetroArch"), "Legend of Zelda, The - A Link to the Past (USA)");
  assert.equal(saveName("USA/Card A/01-GZLE.gci", "Dolphin"), "01-GZLE");
  assert.equal(saveName("", "Dolphin"), "Dolphin");
});

test("change summaries", () => {
  assert.equal(changeSummary(version(), "file", "RetroArch"), "Legend of Zelda, The - A Link to the Past (USA) changed");
  assert.match(changeSummary(version({ changes: null }), "file", "RetroArch"), /cleaned up/);
  const many = version({
    changes: ["a", "b", "c", "d"].map((n) => ({ path: `${n}.gci`, unit: "", change: "added", size: 1 })),
  });
  assert.equal(changeSummary(many, "group", "Dolphin"), "4 files changed: a, b, c and 1 more");
});

test("then and now, from this machine's point of view", () => {
  const v = version() as any;
  assert.equal(thenNow(v.differs[1].files[0].then, v.differs[1].files[0].now, me, machines, now),
    "In this version: Mon 16 Nov, 19:05 · this machine → Now: Today, 14:02 · this machine");
  assert.match(thenNow(v.differs[0].files[0].then, null, me, machines, now), /Now: deleted$/);
});

test("toggles start on only for what that version changed, or the filtered save", () => {
  assert.deepEqual(initialSelection(version(), null), [ZELDA]);
  assert.deepEqual(initialSelection(version(), METROID), [METROID]);
  assert.deepEqual(initialSelection(version(), "Chrono Trigger (USA).srm"), [], "not restorable: didn't exist then");
});

test("confirm spells out the result, the trash and the undo", () => {
  const c = confirmText(version(), data(), "RetroArch", [ZELDA], me, now);
  assert.equal(c.title, "Restore Legend of Zelda, The - A Link to the Past (USA)?");
  assert.match(c.body, /goes back to the version from Yesterday, 21:40 \(Steam Deck\)/);
  assert.match(c.body, /current copy goes to Memcard's trash first/);
  assert.match(c.body, /other machines get the restored save on their next sync/);
  assert.match(c.body, /you can undo this/);
  assert.equal(c.button, "Restore 1 save");
  assert.doesNotMatch(JSON.stringify(c), /Deck's|this Deck/);
});

test("memory cards and removed files get their own warning", () => {
  const card = version({
    differs: [{ unit: "Mcd001.ps2", restorable: true, files: [{ path: "Mcd001.ps2", then: side(at(2026, 10, 15, 16, 2), "m-deck"), now: side(at(2026, 10, 17, 20, 10), "m-self") }] }],
  });
  assert.match(confirmText(card, data("dir"), "PCSX2", ["Mcd001.ps2"], me, now).notes[0], /Mcd001 is a memory card/);

  const whole = version({
    differs: [{ unit: "", restorable: true, files: [
      { path: "USA/Card A/01-GZLE-Wind Waker.gci", then: side(at(2026, 10, 13, 21, 15), "m-deck"), now: side(at(2026, 10, 16, 22, 30), "m-self") },
      { path: "USA/Card A/01-GPVE-Pikmin.gci", then: null, now: side(at(2026, 10, 17, 19, 0), "m-self") },
    ] }],
  });
  const c = confirmText(whole, data("group"), "Dolphin (GameCube)", [], me, now);
  assert.equal(c.title, "Restore Dolphin (GameCube) saves?");
  assert.match(c.notes.join(" "), /removed here: 01-GPVE-Pikmin/);
});

test("restore names every save explicitly, and a whole group needs none", () => {
  assert.deepEqual(restoreArgs("retroarch", 45, "file", [ZELDA, METROID]),
    ["history", "restore", "retroarch", "45", "--unit", ZELDA, "--unit", METROID]);
  assert.deepEqual(restoreArgs("dolphin-gc", 9, "group", ["ignored"], ["--resolve", "dolphin-gc=local"]),
    ["history", "restore", "dolphin-gc", "9", "--resolve", "dolphin-gc=local"]);
});

test("restore is blocked during a game or a pending conflict, with the reason", () => {
  assert.equal(blockedReason(true, false, "RetroArch"), "You can restore after you quit the game.");
  assert.match(blockedReason(false, true, "RetroArch")!, /RetroArch has a conflict waiting/);
  assert.equal(blockedReason(false, false, "RetroArch"), null);
});
