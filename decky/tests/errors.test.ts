import assert from "node:assert/strict";
import { test } from "node:test";
import { errorDetail, errorText, textLines } from "../src/errorText.ts";

test("errorText joins message and hint", () => {
  assert.equal(errorText({ message: "Credentials rejected.", hint: "Use an app password." }), "Credentials rejected. Use an app password.");
  assert.equal(errorText({ message: "Offline.", hint: null }), "Offline.");
  assert.equal(errorText(null), "");
});

test("errorDetail returns the raw output", () => {
  const detail = "2026/11/19 ERROR : webdav: 401 Unauthorized\nFailed to copy";
  assert.equal(errorDetail({ message: "Credentials rejected.", detail: `\n${detail}\n` }), detail);
  // The runner's fallback when the CLI died without stderr is still worth showing.
  assert.equal(errorDetail({ message: "Memcard stopped.", detail: "exit code 1" }), "exit code 1");
});

test("errorDetail is null when there's nothing more to see", () => {
  assert.equal(errorDetail(null), null);
  assert.equal(errorDetail({ message: "Offline." }), null);
  assert.equal(errorDetail({ message: "Offline.", detail: null }), null);
  assert.equal(errorDetail({ message: "Offline.", detail: "  \n" }), null);
  assert.equal(errorDetail({ message: "Offline.", detail: "Offline." }), null);
  assert.equal(errorDetail({ message: "Offline.", hint: "Check Wi-Fi.", detail: "Offline. Check Wi-Fi." }), null);
});

test("textLines splits lines and drops trailing blanks", () => {
  assert.deepEqual(textLines("a\r\nb\rc\n\n  \n"), ["a", "b", "c"]);
  assert.deepEqual(textLines("a\n\nb"), ["a", "", "b"]);
  assert.deepEqual(textLines(""), []);
});
