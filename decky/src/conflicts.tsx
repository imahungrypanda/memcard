import { DialogButton, Focusable, ModalRoot, showModal } from "@decky/ui";
import { useState } from "react";
import { plan, type Me, type Prompt } from "./conflictText";
import type { Choice, FileEntry, GroupResult } from "./types";

// One prompt per conflict (or per group, when every save on one side is missing).
// Nothing is ever resolved without the player choosing. Wording lives in conflictText.ts.

function size(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function when(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function Side({ label, entry }: { label: string; entry: FileEntry | null }) {
  return (
    <div style={{ flex: 1, padding: "6px 8px", background: "rgba(255,255,255,0.05)", borderRadius: 4 }}>
      <div style={{ fontWeight: "bold", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: "0.85em", opacity: 0.85 }}>
        {entry ? [size(entry.size), when(entry.mtime)].filter(Boolean).join(" · ") : "Missing"}
      </div>
    </div>
  );
}

interface Props {
  prompt: Prompt;
  index: number;
  total: number;
  onChoose: (c: Choice | null) => void;
  closeModal?: () => void;
}

function ConflictModal({ prompt: p, index, total, onChoose, closeModal }: Props) {
  const [done, setDone] = useState(false);
  const choose = (c: Choice | null) => {
    if (done) return;
    setDone(true);
    onChoose(c);
    closeModal?.();
  };
  return (
    <ModalRoot onCancel={() => choose(null)} bDisableBackgroundDismiss>
      <div style={{ fontSize: "1.3em", fontWeight: "bold", marginBottom: 6 }}>
        {p.title} {total > 1 ? `(${index + 1} of ${total})` : ""}
      </div>
      <div style={{ marginBottom: 10, opacity: 0.85 }}>{p.explain}</div>
      <div style={{ maxHeight: 160, overflowY: "auto", marginBottom: 10 }}>
        {p.files.slice(0, 6).map((f) => (
          <div key={f.path} style={{ marginBottom: 8 }}>
            <div style={{ fontFamily: "monospace", fontSize: "0.85em", marginBottom: 3 }}>{f.path}</div>
            <div style={{ display: "flex", gap: 8 }}>
              <Side label="On this machine" entry={f.local} />
              <Side label={p.cloudLabel} entry={f.remote} />
            </div>
          </div>
        ))}
        {p.files.length > 6 && <div style={{ opacity: 0.7 }}>…and {p.files.length - 6} more files.</div>}
      </div>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }} flow-children="vertical">
        {p.options.map((o) => (
          <DialogButton key={o.label} style={{ textAlign: "left", padding: "8px 12px" }} onClick={() => choose(o.choice)}>
            <div style={{ fontWeight: "bold" }}>{o.label}</div>
            <div style={{ fontSize: "0.8em", opacity: 0.8, whiteSpace: "normal" }}>{o.detail}</div>
          </DialogButton>
        ))}
      </Focusable>
    </ModalRoot>
  );
}

function ask(props: Omit<Props, "onChoose">): Promise<Choice | null> {
  return new Promise((resolve) => {
    showModal(<ConflictModal {...props} onChoose={resolve} />);
  });
}

/** Ask about every conflict; returns `--resolve` arguments for the ones decided. */
export async function askResolutions(results: GroupResult[], me: Me): Promise<string[]> {
  const todo = plan(results, me);
  const specs: string[] = [];
  for (let i = 0; i < todo.length; i++) {
    const { group, keys, prompt } = todo[i];
    const choice = await ask({ prompt, index: i, total: todo.length });
    if (choice) for (const key of keys) specs.push("--resolve", `${group}:${key}=${choice}`);
  }
  return specs;
}
