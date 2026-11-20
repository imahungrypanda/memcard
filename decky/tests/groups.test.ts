import assert from "node:assert/strict";
import { test } from "node:test";
import { groupLine, groupsSummary, planSummary } from "../src/groupsText.ts";

const group = (id: string, appids: number[] = [1]) => ({ id, label: id, unit: "group", path: `/saves/${id}`, emulator: null, appids });
const plan = (group: string, push = 0, pull = 0, conflict = 0, error?: string) => ({
  group,
  remote_generation: 1,
  summary: { noop: 0, push, pull, conflict },
  units: [],
  ...(error ? { error: { code: "error", message: error } } : {}),
});

test("planSummary: nothing before a check, counts after, up to date when empty", () => {
  assert.equal(planSummary(undefined), "");
  assert.equal(planSummary(plan("a", 2, 0, 1)), "2 to upload, 1 conflict(s)");
  assert.equal(planSummary(plan("a")), "✓ up to date");
  assert.equal(planSummary(plan("a", 0, 0, 0, "offline")), "✗ offline");
});

test("groupLine: mapped games, with the check's result in front once there is one", () => {
  assert.equal(groupLine(group("pcsx2", [1, 2]), undefined), "2 game(s) mapped");
  assert.equal(groupLine(group("pcsx2", []), undefined), "manual sync only");
  assert.equal(groupLine(group("pcsx2"), plan("pcsx2", 0, 3)), "3 to download · 1 game(s) mapped");
});

test("groupsSummary: a count and a hint before a check", () => {
  assert.equal(groupsSummary([group("a")], {}, true), "1 group. Pick one to see and restore its older saves.");
  assert.equal(groupsSummary([group("a"), group("b")], {}, true), "2 groups. Pick one to see and restore its older saves.");
});

test("groupsSummary: without cloud storage, says to connect it", () => {
  assert.equal(groupsSummary([group("a"), group("b")], { a: plan("a") }, false), "2 groups. Connect cloud storage to see their older saves.");
});

test("groupsSummary: totals across checked groups, failures counted apart", () => {
  const groups = [group("a"), group("b"), group("c")];
  assert.equal(groupsSummary(groups, { a: plan("a"), b: plan("b"), c: plan("c") }, true), "3 groups · ✓ up to date");
  assert.equal(
    groupsSummary(groups, { a: plan("a", 2), b: plan("b", 1, 0, 1), c: plan("c", 5, 0, 0, "offline") }, true),
    "3 groups · 3 to upload, 1 conflict(s), 1 couldn't be checked",
  );
});

test("groupsSummary: a group added since the check is never called up to date", () => {
  const groups = [group("a"), group("b"), group("new")];
  assert.equal(groupsSummary(groups, { a: plan("a"), b: plan("b") }, true), "3 groups · 1 not checked yet");
  assert.equal(groupsSummary(groups, { a: plan("a", 0, 2), b: plan("b") }, true), "3 groups · 2 to download, 1 not checked yet");
});

test("groupLine: a failed check shows its error", () => {
  assert.equal(groupLine(group("pcsx2"), plan("pcsx2", 0, 0, 0, "offline")), "✗ offline · 1 game(s) mapped");
});
