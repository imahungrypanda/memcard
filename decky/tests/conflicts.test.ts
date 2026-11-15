import assert from "node:assert/strict";
import { test } from "node:test";
import { plan, prompt, writer } from "../src/conflictText.ts";

const me = { id: "m-self", name: "Steam Machine" };
const machines = { "m-self": "Steam Machine", "m-deck": "Steam Deck" };
const entry = (by: string) => ({ sha256: "a".repeat(64), size: 10, written_by: by });
const file = (path: string, local: unknown, remote: unknown) =>
  ({ path, verdict: "conflict", local, remote, base: entry("m-self") }) as never;
const unit = (key: string, files: never[], reason: string | null = null) => ({ key, verdict: "conflict", reason, files }) as never;

test("never says Deck for this machine; our own cloud copy reads 'this machine, earlier'", () => {
  assert.equal(writer(entry("m-self"), machines, me), "this machine, earlier");
  assert.equal(writer(entry("m-deck"), machines, me), "Steam Deck");
  assert.equal(writer(entry("m-gone"), machines, me), "another machine");
  const p = prompt("gba", [unit("a.srm", [file("a.srm", entry("m-self"), entry("m-deck"))])], machines, me);
  assert.doesNotMatch(JSON.stringify(p), /Deck's|this Deck/);
  assert.equal(p.cloudLabel, "In the cloud (saved by Steam Deck)");
});

test("missing local saves: restore first, delete spelled out, no keep-both", () => {
  const p = prompt("gba", [unit("", [file("a.srm", null, entry("m-self"))], "local_wiped")], machines, me);
  assert.equal(p.title, "Saves missing on this machine");
  assert.deepEqual(p.options.map((o) => [o.choice, o.label]), [
    ["remote", "Restore from the cloud"],
    ["local", "Delete them everywhere"],
    [null, "Decide later"],
  ]);
});

test("missing cloud saves: upload first", () => {
  const p = prompt("gba", [unit("", [file("a.srm", entry("m-self"), null)], "remote_wiped")], machines, me);
  assert.deepEqual(p.options.map((o) => o.choice), ["local", "remote", null]);
  assert.equal(p.options[0].label, "Upload this machine's saves");
});

test("both changed: cloud, this machine, keep both, later", () => {
  const p = prompt("gba", [unit("a.srm", [file("a.srm", entry("m-self"), entry("m-deck"))])], machines, me);
  assert.deepEqual(p.options.map((o) => o.choice), ["remote", "local", "both", null]);
});

test("one side deleted: options say delete, no keep-both", () => {
  const gone = prompt("gba", [unit("a.srm", [file("a.srm", entry("m-self"), null)])], machines, me);
  assert.equal(gone.options[0].label, "Delete it here too");
  assert.ok(!gone.options.some((o) => o.choice === "both"));
  const here = prompt("gba", [unit("a.srm", [file("a.srm", null, entry("m-deck"))])], machines, me);
  assert.equal(here.options[1].label, "Delete it from the cloud too");
});

test("a wiped group is one prompt resolving every unit; ordinary conflicts stay one each", () => {
  const todo = plan(
    [
      {
        group: "gba",
        machines,
        conflicts: [
          unit("a", [file("a/1", null, entry("m-self"))], "local_wiped"),
          unit("b", [file("b/1", null, entry("m-self"))], "local_wiped"),
        ],
      },
      {
        group: "psx",
        machines,
        conflicts: [unit("x", [file("x", entry("m-self"), entry("m-deck"))]), unit("y", [file("y", entry("m-self"), entry("m-deck"))])],
      },
    ],
    me,
  );
  assert.deepEqual(todo.map((t) => [t.group, t.keys]), [["gba", ["a", "b"]], ["psx", ["x"]], ["psx", ["y"]]]);
  assert.match(todo[0].prompt.explain, /cloud still has 2 files/);
});
