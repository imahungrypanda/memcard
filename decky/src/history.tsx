import { toaster } from "@decky/api";
import { DialogButton, Dropdown, Focusable, ModalRoot, showModal, Spinner, ToggleField } from "@decky/ui";
import { useEffect, useState } from "react";
import { errorText, memcard } from "./backend";
import { askResolutions } from "./conflicts";
import {
  changeSummary,
  confirmText,
  initialSelection,
  restoreArgs,
  restoredToast,
  saveName,
  thenNow,
  versionTitle,
  type Me,
} from "./historyText";
import type { GroupInfo, HistoryData, HistoryVersion, MemcardError, RestoreData } from "./types";

// Cloud versions for one save group: list -> one version -> confirm -> restoring.
// Browsing works any time; Restore is blocked while a game runs or a conflict waits.
// All wording lives in historyText.ts; all logic in `memcard history`.

// Same as the conflict prompt: let two-line buttons wrap instead of overflowing.
const ROW = {
  width: "100%",
  minWidth: 0,
  height: "auto",
  minHeight: 0,
  lineHeight: 1.3,
  whiteSpace: "normal",
  textAlign: "left",
  padding: "8px 12px",
  boxSizing: "border-box",
} as const;

const PILL = { fontSize: "0.7em", fontWeight: "bold", letterSpacing: "0.06em", padding: "1px 6px", borderRadius: 8, marginLeft: 8 } as const;

interface Props {
  group: GroupInfo;
  me: Me;
  blocked: () => string | null;
  onChanged: () => void;
  closeModal?: () => void;
}

type View = "list" | "detail" | "confirm" | "working";

function HistoryModal({ group, me, blocked, onChanged, closeModal }: Props) {
  const label = group.label || group.id;
  const [view, setView] = useState<View>("list");
  const [data, setData] = useState<HistoryData | null>(null);
  const [saves, setSaves] = useState<string[]>([]); // filter choices, from the unfiltered list
  const [filter, setFilter] = useState<string | null>(null);
  const [version, setVersion] = useState<HistoryVersion | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<MemcardError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async (unit: string | null) => {
    setLoading(true);
    setError(null);
    const r = await memcard<HistoryData>(["history", "list", group.id, ...(unit ? ["--unit", unit] : [])]);
    setLoading(false);
    if (r.error || !r.result) return setError(r.error ?? { code: "error", message: "Couldn't read the cloud versions." });
    setData(r.result);
    if (!unit) {
      const seen = new Set(r.result.versions.flatMap((v) => (v.changes ?? []).map((c) => c.unit)));
      setSaves([...seen].filter(Boolean).sort());
    }
  };

  useEffect(() => {
    load(null);
  }, []);

  const open = (v: HistoryVersion) => {
    setVersion(v);
    setPicked(initialSelection(v, filter));
    setError(null);
    setView("detail");
  };

  const restore = async (resolve: string[] = []) => {
    if (!data || !version) return;
    setView("working");
    setError(null);
    const units = data.unit === "group" ? [] : picked;
    const r = await memcard<RestoreData>(restoreArgs(group.id, version.generation, data.unit, units, resolve));
    const out = r.result;
    if (!out || (!out.restored && r.error)) {
      setError(r.error ?? { code: "error", message: "The restore didn't finish." });
      return setView("detail");
    }
    if (!out.restored) {
      const before = out.results[0];
      if (before?.conflicts.length && !resolve.length) {
        const specs = await askResolutions(out.results, me);
        if (specs.length) return restore(specs);
      }
      setError(before?.error ?? { code: "conflicts", message: `Nothing was restored: ${label} has a conflict waiting.` });
      return setView("detail");
    }
    toaster.toast({ title: `Memcard · ${label}`, body: restoredToast(label, data.unit, units, version), duration: 6000 });
    const after = out.results[out.results.length - 1];
    if (after?.conflicts.length) {
      toaster.toast({ title: `Memcard · ${label}`, body: "A save changed here during the restore. It's listed under Resolve conflicts.", duration: 6000 });
    }
    onChanged();
    setView("list");
    setFilter(null);
    load(null);
  };

  const header = (title: string, sub?: string) => (
    <>
      <div style={{ fontSize: "1.3em", fontWeight: "bold", marginBottom: 4 }}>{title}</div>
      {sub && <div style={{ marginBottom: 10, opacity: 0.75 }}>{sub}</div>}
    </>
  );
  const problem = error && <div style={{ color: "#f2b33d", margin: "8px 0" }}>✗ {errorText(error)}</div>;

  if (view === "list" || !data || !version) {
    const shown = data?.versions ?? [];
    return (
      <ModalRoot onCancel={closeModal} bAllowFullSize>
        {header(`Cloud versions · ${label}`, "Newest first. Pick a version to see what you can restore from it.")}
        {data && data.unit !== "group" && saves.length > 1 && (
          <div style={{ marginBottom: 10 }}>
            <Dropdown
              menuLabel="Show versions that changed"
              strDefaultLabel="Any save"
              rgOptions={[{ data: null, label: "Any save" }, ...saves.map((s) => ({ data: s, label: saveName(s, label) }))]}
              selectedOption={filter}
              onChange={(o) => {
                setFilter(o.data);
                load(o.data);
              }}
            />
          </div>
        )}
        {problem}
        {loading && <Spinner style={{ width: 32 }} />}
        {!loading && data && !shown.length && (
          <div style={{ opacity: 0.75 }}>{filter ? "None of the recent versions changed that save." : "Nothing in the cloud for this group yet."}</div>
        )}
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 360, overflowY: "auto" }} flow-children="vertical">
          {data &&
            shown.map((v) => (
              <DialogButton key={v.generation} style={ROW} onClick={() => open(v)}>
                <div style={{ fontWeight: "bold" }}>
                  {versionTitle(v, data, me)}
                  {v.current && <span style={{ ...PILL, background: "rgba(108,191,74,.2)", color: "#6cbf4a" }}>CURRENT</span>}
                  {v.restored_from !== null && !v.current && <span style={{ ...PILL, background: "rgba(58,160,255,.18)", color: "#3aa0ff" }}>RESTORE</span>}
                </div>
                <div style={{ fontSize: "0.8em", opacity: 0.8 }}>
                  {v.restored_from !== null ? `Restored from version ${v.restored_from}. ` : ""}
                  {changeSummary(v, data.unit, label)}
                </div>
              </DialogButton>
            ))}
        </Focusable>
        {data?.more && <div style={{ fontSize: "0.8em", opacity: 0.6, marginTop: 6 }}>Older versions are in the cloud too; `memcard history list` shows them.</div>}
        <DialogButton style={{ marginTop: 10 }} onClick={closeModal}>
          Close
        </DialogButton>
      </ModalRoot>
    );
  }

  const reason = blocked();
  const restorable = version.differs.filter((d) => d.restorable);
  const missing = version.differs.filter((d) => !d.restorable);
  const units = data.unit === "group" ? restorable.map((d) => d.unit) : picked;
  const confirm = confirmText(version, data, label, units, me);
  const canRestore = !version.current && restorable.length > 0;

  if (view === "confirm") {
    return (
      <ModalRoot onCancel={() => setView("detail")}>
        {header(confirm.title)}
        <div style={{ marginBottom: 8 }}>{confirm.body}</div>
        {confirm.notes.map((n) => (
          <div key={n} style={{ marginBottom: 8, padding: "6px 8px", borderRadius: 4, background: "rgba(242,179,61,.1)", color: "#f6d08a" }}>
            {n}
          </div>
        ))}
        <Focusable style={{ display: "flex", gap: 8 }} flow-children="horizontal">
          <DialogButton onClick={() => setView("detail")}>Cancel</DialogButton>
          <DialogButton disabled={!!reason} onClick={() => restore()}>
            Restore
          </DialogButton>
        </Focusable>
      </ModalRoot>
    );
  }

  if (view === "working") {
    return (
      <ModalRoot onCancel={closeModal}>
        {header("Restoring…", `Syncing ${label} first, saving the restored version to the cloud, then downloading it here.`)}
        <Spinner style={{ width: 32 }} />
        <div style={{ fontSize: "0.85em", opacity: 0.7, marginTop: 8 }}>You can close this; Memcard finishes in the background.</div>
      </ModalRoot>
    );
  }

  return (
    <ModalRoot onCancel={() => setView("list")} bAllowFullSize>
      {header(versionTitle(version, data, me), changeSummary(version, data.unit, label))}
      {version.current && <div style={{ opacity: 0.75 }}>This is the current version. Pick an older one to restore from.</div>}
      {!version.current && !restorable.length && <div style={{ opacity: 0.75 }}>Every save in this version matches the current one, so there's nothing to restore.</div>}
      {canRestore && data.unit === "group" && (
        <div style={{ opacity: 0.75, marginBottom: 6 }}>{label} saves are restored together, so a game is never left half from one version and half from another.</div>
      )}
      {canRestore && data.unit !== "group" && <div style={{ opacity: 0.75, marginBottom: 6 }}>Choose the saves to put back. The rest stay as they are now.</div>}
      <Focusable style={{ maxHeight: 300, overflowY: "auto" }} flow-children="vertical">
        {restorable.map((d) =>
          data.unit === "group" ? (
            d.files.map((f) => (
              <div key={f.path} style={{ padding: "6px 0", borderBottom: "1px solid rgba(255,255,255,.08)" }}>
                <div style={{ fontWeight: "bold" }}>{saveName(f.path, label)}</div>
                <div style={{ fontSize: "0.8em", opacity: 0.75 }}>{thenNow(f.then, f.now, me, data.machines)}</div>
              </div>
            ))
          ) : (
            <ToggleField
              key={d.unit}
              label={saveName(d.unit, label)}
              description={thenNow(d.files[0]?.then ?? null, d.files[0]?.now ?? null, me, data.machines)}
              checked={picked.includes(d.unit)}
              onChange={(on) => setPicked(on ? [...picked, d.unit] : picked.filter((u) => u !== d.unit))}
            />
          ),
        )}
        {missing.map((d) => (
          <div key={d.unit} style={{ padding: "6px 0", opacity: 0.5 }}>
            <div style={{ fontWeight: "bold" }}>{saveName(d.unit, label)}</div>
            <div style={{ fontSize: "0.8em" }}>Not saved yet in this version, so it can't be restored from it.</div>
          </div>
        ))}
      </Focusable>
      {problem}
      {canRestore && reason && <div style={{ color: "#f2b33d", margin: "8px 0" }}>{reason}</div>}
      <Focusable style={{ display: "flex", gap: 8, marginTop: 10 }} flow-children="horizontal">
        <DialogButton onClick={() => setView("list")}>Back</DialogButton>
        {canRestore && (
          <DialogButton disabled={!!reason || !units.length} onClick={() => setView("confirm")}>
            {confirm.button}
          </DialogButton>
        )}
      </Focusable>
    </ModalRoot>
  );
}

export function showHistory(group: GroupInfo, me: Me, blocked: () => string | null, onChanged: () => void): void {
  showModal(<HistoryModal group={group} me={me} blocked={blocked} onChanged={onChanged} />);
}
