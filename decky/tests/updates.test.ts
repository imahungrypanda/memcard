import assert from "node:assert/strict";
import { test } from "node:test";
import { availableUpdate, compareVersions, parseRelease } from "../src/updates.ts";

const HASH = "1a8d37ac28499036f2f2694e25176f54b53daaae69fa7663eabc1415485a1181";
const release = (tag: string, extra: Record<string, unknown> = {}) => ({
  tag_name: tag,
  draft: false,
  prerelease: false,
  html_url: `https://github.com/imahungrypanda/memcard/releases/tag/${tag}`,
  assets: [
    { name: "memcard", browser_download_url: `https://github.com/imahungrypanda/memcard/releases/download/${tag}/memcard`, digest: `sha256:${"0".repeat(64)}` },
    {
      name: "memcard-decky.zip",
      browser_download_url: `https://github.com/imahungrypanda/memcard/releases/download/${tag}/memcard-decky.zip`,
      digest: `sha256:${HASH}`,
    },
  ],
  ...extra,
});

test("compareVersions is numeric, not lexical", () => {
  assert.equal(compareVersions("0.1.10", "0.1.9"), 1);
  assert.equal(compareVersions("v0.2.0", "0.1.99"), 1);
  assert.equal(compareVersions("0.1.2", "0.1.2"), 0);
  assert.equal(compareVersions("0.1.2", "0.1.3"), -1);
  assert.equal(compareVersions("0.1.2-dev", "0.1.3"), null);
});

test("parseRelease picks the plugin zip and its sha256", () => {
  assert.deepEqual(parseRelease(release("v0.1.5")), {
    version: "0.1.5",
    artifact: "https://github.com/imahungrypanda/memcard/releases/download/v0.1.5/memcard-decky.zip",
    hash: HASH,
    page: "https://github.com/imahungrypanda/memcard/releases/tag/v0.1.5",
  });
});

test("parseRelease refuses anything it can't verify or that isn't a release", () => {
  assert.equal(parseRelease(null), null);
  assert.equal(parseRelease({ message: "API rate limit exceeded" }), null);
  assert.equal(parseRelease(release("v0.1.5", { prerelease: true })), null);
  assert.equal(parseRelease(release("v0.1.5", { draft: true })), null);
  assert.equal(parseRelease(release("nightly")), null);
  const noDigest = release("v0.1.5");
  delete (noDigest.assets[1] as Record<string, unknown>).digest;
  assert.equal(parseRelease(noDigest), null);
  const elsewhere = release("v0.1.5");
  elsewhere.assets[1].browser_download_url = "https://evil.example/memcard-decky.zip";
  assert.equal(parseRelease(elsewhere), null);
});

test("availableUpdate only offers newer releases", () => {
  assert.equal(availableUpdate("0.1.5", release("v0.1.5")), null);
  assert.equal(availableUpdate("0.1.6", release("v0.1.5")), null);
  assert.equal(availableUpdate("0.1.4", release("v0.1.5"))?.version, "0.1.5");
  assert.equal(availableUpdate("0.1.9", release("v0.1.10"))?.version, "0.1.10");
  // a local build without a release version is offered the release
  assert.equal(availableUpdate(null, release("v0.1.5"))?.version, "0.1.5");
  assert.equal(availableUpdate("dev", release("v0.1.5"))?.version, "0.1.5");
});
