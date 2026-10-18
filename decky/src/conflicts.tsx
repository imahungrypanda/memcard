import { DialogButton, Focusable, ModalRoot, showModal } from "@decky/ui";
import { useState } from "react";
import type { Choice, FileChange, GroupResult, UnitPlan } from "./types";

// One modal per conflicting unit. Nothing is ever resolved without the player choosing.

function size(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function when(ts?: string | null): string {
  if (!ts) return "unknown time";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function Side({ label, entry }: { label: string; entry: FileChange["local"] }) {
  return (
    <div style={{ flex: 1, padding: "6px 8px", background: "rgba(255,255,255,0.05)", borderRadius: 4 }}>
      <div style={{ fontWeight: "bold", marginBottom: 2 }}>{label}</div>
      {entry ? (
        <div style={{ fontSize: "0.85em", opacity: 0.85 }}>
          {size(entry.size)} · modified {when(entry.mtime)}
        </div>
      ) : (
        <div style={{ fontSize: "0.85em", opacity: 0.85 }}>deleted</div>
      )}
    </div>
  );
}

const REASONS: Record<string, string> = {
  local_wiped:
    "Every save in this group is missing on this Deck, but the cloud still has them. Usually that's an unmounted SD card. Keep cloud restores them; keep Deck deletes them everywhere.",
  remote_wiped:
    "The cloud has lost every save in this group, but this Deck still has them (usually a reset or changed cloud folder). Keep Deck re-uploads them.",
};

interface Props {
  group: string;
  unit: UnitPlan;
  machines: Record<string, string>;
  me: string;
  index: number;
  total: number;
  onChoose: (c: Choice | null) => void;
  closeModal?: () => void;
}

function ConflictModal({ group, unit, machines, me, index, total, onChoose, closeModal }: Props) {
  const [done, setDone] = useState(false);
  const changed = unit.files.filter((f) => f.verdict !== "noop");
  const files = changed.length ? changed : unit.files;
  const other =
    files.map((f) => f.remote?.written_by && machines[f.remote.written_by]).find(Boolean) ?? "the other machine";
  const choose = (c: Choice | null) => {
    if (done) return;
    setDone(true);
    onChoose(c);
    closeModal?.();
  };
  return (
    <ModalRoot onCancel={() => choose(null)} bDisableBackgroundDismiss>
      <div style={{ fontSize: "1.3em", fontWeight: "bold", marginBottom: 6 }}>
        Save conflict {total > 1 ? `(${index + 1} of ${total})` : ""}
      </div>
      <div style={{ marginBottom: 8, opacity: 0.85 }}>
        {group} › {unit.key || "all files"}: changed on this Deck and on {other} since the last sync.
      </div>
      {unit.reason && <div style={{ marginBottom: 8, color: "#ffc82c" }}>{REASONS[unit.reason]}</div>}
      <div style={{ maxHeight: 220, overflowY: "auto", marginBottom: 10 }}>
        {files.slice(0, 8).map((f) => (
          <div key={f.path} style={{ marginBottom: 8 }}>
            <div style={{ fontFamily: "monospace", fontSize: "0.85em", marginBottom: 3 }}>{f.path}</div>
            <div style={{ display: "flex", gap: 8 }}>
              <Side label={`This Deck (${me})`} entry={f.local} />
              <Side
                label={`Cloud (from ${(f.remote?.written_by && machines[f.remote.written_by]) || other})`}
                entry={f.remote}
              />
            </div>
          </div>
        ))}
        {files.length > 8 && <div style={{ opacity: 0.7 }}>…and {files.length - 8} more files that move with these.</div>}
      </div>
      <Focusable style={{ display: "flex", gap: 8 }} flow-children="horizontal">
        <DialogButton onClick={() => choose("local")}>Keep this Deck's</DialogButton>
        <DialogButton onClick={() => choose("remote")}>Keep {other}'s</DialogButton>
        <DialogButton onClick={() => choose("both")}>Keep both</DialogButton>
        <DialogButton onClick={() => choose(null)}>Decide later</DialogButton>
      </Focusable>
      <div style={{ marginTop: 8, fontSize: "0.8em", opacity: 0.7 }}>
        Whatever loses is copied to Memcard's trash first, so it can be restored.
      </div>
    </ModalRoot>
  );
}

function ask(props: Omit<Props, "onChoose">): Promise<Choice | null> {
  return new Promise((resolve) => {
    showModal(<ConflictModal {...props} onChoose={resolve} />);
  });
}

/** Ask about every conflicting unit; returns `--resolve` arguments for the ones decided. */
export async function askResolutions(results: GroupResult[], me: string): Promise<string[]> {
  const todo = results.flatMap((r) => r.conflicts.map((unit) => ({ r, unit })));
  const specs: string[] = [];
  for (let i = 0; i < todo.length; i++) {
    const { r, unit } = todo[i];
    const choice = await ask({ group: r.group, unit, machines: r.machines, me, index: i, total: todo.length });
    if (choice) specs.push("--resolve", `${r.group}:${unit.key}=${choice}`);
  }
  return specs;
}
