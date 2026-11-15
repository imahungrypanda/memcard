// What the conflict prompt says. Pure (no @decky imports) so Node can test it.
//
// Rules: never call this machine a "Deck" (it may be a Steam Machine or a PC); say
// "this machine" and "the cloud", and name other machines only as who last saved.
// Every option states its result, because the choice can delete saves.

import type { Choice, FileChange, FileEntry, UnitPlan } from "./types";

export interface Me {
  id: string;
  name: string;
}

export interface Option {
  choice: Choice | null; // null = decide later
  label: string;
  detail: string;
}

export interface Prompt {
  title: string;
  explain: string;
  files: FileChange[];
  cloudLabel: string;
  options: Option[];
}

function count(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "Steam Deck", or "this machine, earlier" when we wrote the cloud copy ourselves. */
export function writer(entry: FileEntry | null | undefined, machines: Record<string, string>, me: Me): string | null {
  const id = entry?.written_by;
  if (!id) return null;
  if (id === me.id) return "this machine, earlier";
  return machines[id] || "another machine";
}

const LATER: Option = {
  choice: null,
  label: "Decide later",
  detail: "Change nothing for now. The conflict stays listed in the Memcard panel.",
};

/** One prompt for one or more units of the same group that share a cause. */
export function prompt(group: string, units: UnitPlan[], machines: Record<string, string>, me: Me): Prompt {
  const all = units.flatMap((u) => u.files);
  const changed = all.filter((f) => f.verdict !== "noop");
  const files = changed.length ? changed : all;
  const by = files.map((f) => writer(f.remote, machines, me)).find(Boolean);
  const cloudLabel = by ? `In the cloud (saved by ${by})` : "In the cloud";
  const reason = units[0]?.reason ?? null;

  if (reason === "local_wiped") {
    const n = files.filter((f) => f.remote).length;
    return {
      title: "Saves missing on this machine",
      explain:
        `Every save in ${group} is gone from this machine, but the cloud still has ${count(n, "file")}. ` +
        "This usually means a reinstall, a drive that isn't mounted, or saves that were moved.",
      files,
      cloudLabel,
      options: [
        { choice: "remote", label: "Restore from the cloud", detail: "Download them back to this machine." },
        {
          choice: "local",
          label: "Delete them everywhere",
          detail: "Remove them from the cloud too. Your other machines delete their copies on their next sync.",
        },
        LATER,
      ],
    };
  }
  if (reason === "remote_wiped") {
    const n = files.filter((f) => f.local).length;
    return {
      title: "Saves missing in the cloud",
      explain:
        `The cloud has lost every save in ${group}, but this machine still has ${count(n, "file")}. ` +
        "This usually means the cloud folder was reset or changed.",
      files,
      cloudLabel,
      options: [
        { choice: "local", label: "Upload this machine's saves", detail: "Put them back in the cloud." },
        {
          choice: "remote",
          label: "Delete them from this machine",
          detail: "They go to Memcard's trash first, so you can still restore them.",
        },
        LATER,
      ],
    };
  }

  const anyLocal = files.some((f) => f.local);
  const anyRemote = files.some((f) => f.remote);
  const what = files.length === 1 ? "This save" : `These ${files.length} save files`;
  const options: Option[] = [
    anyRemote
      ? { choice: "remote", label: "Use the cloud save", detail: "Replace this machine's. It goes to Memcard's trash first." }
      : { choice: "remote", label: "Delete it here too", detail: "The cloud copy was deleted. This machine's goes to Memcard's trash." },
    anyLocal
      ? {
          choice: "local",
          label: "Use this machine's save",
          detail: "Upload it, replacing the cloud copy. Your other machines get it on their next sync.",
        }
      : { choice: "local", label: "Delete it from the cloud too", detail: "It was deleted on this machine. Your other machines delete theirs on their next sync." },
  ];
  if (anyLocal && anyRemote) {
    options.push({
      choice: "both",
      label: "Keep both",
      detail: "Use the cloud save, and keep this machine's as a copy (renamed beside it, or in Memcard's trash).",
    });
  }
  options.push(LATER);
  return {
    title: "Save changed in two places",
    explain: `${what} in ${group} changed on this machine and in the cloud since the last sync, so Memcard can't tell which one to keep.`,
    files,
    cloudLabel,
    options,
  };
}

/**
 * Prompts to show, in order. Units that lost every file on one side share a cause, so a group
 * gets one prompt for all of them instead of one per file. Each prompt resolves its `keys`.
 */
export function plan(results: { group: string; conflicts: UnitPlan[]; machines: Record<string, string> }[], me: Me) {
  const out: { group: string; keys: string[]; prompt: Prompt }[] = [];
  for (const r of results) {
    const wiped = new Map<string, UnitPlan[]>();
    for (const u of r.conflicts) {
      if (u.reason) wiped.set(u.reason, [...(wiped.get(u.reason) ?? []), u]);
      else out.push({ group: r.group, keys: [u.key], prompt: prompt(r.group, [u], r.machines, me) });
    }
    for (const units of wiped.values()) {
      out.push({ group: r.group, keys: units.map((u) => u.key), prompt: prompt(r.group, units, r.machines, me) });
    }
  }
  return out;
}
