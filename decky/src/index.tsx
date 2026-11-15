import { definePlugin, toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  Field,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  showModal,
  Spinner,
  staticClasses,
  ToggleField,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { FaSdCard } from "react-icons/fa";
import {
  cachedGroups,
  currentSettings,
  environment,
  errorText,
  loadSettings,
  memcard,
  onGroups,
  refreshGroups,
  updateSetting,
  type Settings,
} from "./backend";
import { askResolutions } from "./conflicts";
import { controller, registerLaunchHooks } from "./launch";
import { checkForUpdate, installUpdate, onUpdateState, startUpdateChecks, updateState } from "./updater";
import { describe } from "./launchController";
import { installRclone, showSetup } from "./setup";
import type { GroupPlan, GroupsData, MemcardError, SyncData } from "./types";

function toast(body: string, title = "Memcard") {
  toaster.toast({ title, body, duration: 4000 });
}

function summary(plan: GroupPlan | undefined): string {
  if (!plan) return "";
  if (plan.error) return `✗ ${plan.error.message}`;
  const s = plan.summary;
  const parts = [s.push && `${s.push} to upload`, s.pull && `${s.pull} to download`, s.conflict && `${s.conflict} conflict(s)`].filter(Boolean);
  return parts.length ? parts.join(", ") : "✓ up to date";
}

function showText(title: string, text: string) {
  showModal(
    <ModalRoot bAllowFullSize>
      <div style={{ fontSize: "1.2em", fontWeight: "bold", marginBottom: 8 }}>{title}</div>
      <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.75em", maxHeight: 400, overflowY: "auto" }}>{text}</pre>
    </ModalRoot>,
  );
}

/** Sync, then ask about any conflicts and apply the answers. Used by "Sync now" and "Resolve conflicts". */
async function syncAndResolve(groups: string[] = []): Promise<void> {
  const first = await memcard<SyncData>(["sync", ...groups, "--non-interactive"]);
  if (first.error) return toast(errorText(first.error), "Memcard: sync failed");
  const results = first.result?.results ?? [];
  for (const r of results.filter((r) => r.status === "error" || r.status === "skipped")) {
    toast(`${r.group}: ${errorText(r.error)}`);
  }
  let final = first.result;
  if (results.some((r) => r.conflicts.length)) {
    const resolve = await askResolutions(results, cachedGroups()?.machine ?? { id: "", name: "this machine" });
    if (resolve.length) {
      const second = await memcard<SyncData>(["sync", ...groups, ...resolve]);
      if (second.error) return toast(errorText(second.error), "Memcard: sync failed");
      final = second.result;
    }
  }
  const d = describe(final);
  toast(d.conflicts ? `${d.text}. ${d.conflicts} conflict(s) left for later.` : d.text);
}

function Content() {
  const [groups, setGroups] = useState<GroupsData | null>(cachedGroups());
  const [configured, setConfigured] = useState(true);
  const [settings, setSettings] = useState<Settings>(currentSettings());
  const [plans, setPlans] = useState<Record<string, GroupPlan>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<MemcardError | null>(null);
  const [playing, setPlaying] = useState(controller.isRunning());
  const [upd, setUpd] = useState(updateState());
  const [launchBusy, setLaunchBusy] = useState(controller.isBusy());
  const [checking, setChecking] = useState(false);

  const reload = async () => {
    const env = await environment();
    setConfigured(env.configured);
    if (env.configured) await refreshGroups();
  };

  useEffect(() => {
    const off = onGroups(setGroups);
    const offRunning = controller.onChange(() => {
      setPlaying(controller.isRunning());
      setLaunchBusy(controller.isBusy());
    });
    const offUpdates = onUpdateState(setUpd);
    reload();
    return () => {
      off();
      offRunning();
      offUpdates();
    };
  }, []);

  const task = async (label: string, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(label);
    setError(null);
    try {
      await fn();
    } finally {
      setBusy(null);
      refreshGroups();
    }
  };

  if (!configured) {
    return (
      <PanelSection title="Memcard">
        <PanelSectionRow>
          <Field description="Sync your saves with your own cloud storage. Takes a few minutes." />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => showSetup(false)}>
            Set up Memcard
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const pending = Object.keys(groups?.pending ?? {});
  // Never sync under a running game: core refuses too, this just explains it up front.
  const blocked = !!busy || playing;
  return (
    <>
      <PanelSection title="Status">
        <PanelSectionRow>
          <Field
            label={groups?.machine.name ?? "…"}
            description={groups?.remote ? `↔ ${groups.remote.spec}` : "No cloud storage connected yet."}
          />
        </PanelSectionRow>
        {!groups?.remote && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => showSetup(true)}>
              Connect cloud storage
            </ButtonItem>
          </PanelSectionRow>
        )}
        {playing && (
          <PanelSectionRow>
            <Field description="A game is running. Syncing waits until you quit it; that happens automatically." />
          </PanelSectionRow>
        )}
        {pending.length > 0 && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={blocked}
              description={`Waiting for a decision: ${pending.join(", ")}`}
              onClick={() => task("Resolving…", () => syncAndResolve(pending))}
            >
              Resolve conflicts
            </ButtonItem>
          </PanelSectionRow>
        )}
        {groups?.remote && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={blocked} onClick={() => task("Syncing…", () => syncAndResolve())}>
              Sync now
            </ButtonItem>
          </PanelSectionRow>
        )}
        {busy && (
          <PanelSectionRow>
            <Field label={busy}>
              <Spinner style={{ width: 24 }} />
            </Field>
          </PanelSectionRow>
        )}
        {error && (
          <PanelSectionRow>
            <Field description={`✗ ${errorText(error)}`} />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Automatic">
        <PanelSectionRow>
          <ToggleField
            label="Sync around games"
            description="Before a mapped game starts and after it exits."
            checked={settings.auto_sync}
            onChange={async (v) => setSettings(await updateSetting("auto_sync", v))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Notify after syncing"
            checked={settings.notify}
            onChange={async (v) => setSettings(await updateSetting("notify", v))}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Updates">
        {upd.update ? (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={launchBusy || !!busy}
              description={launchBusy ? "Available after you quit the game and its saves finish syncing." : upd.installed ? `Installed: ${upd.installed}` : undefined}
              onClick={() => installUpdate(upd.update!)}
            >
              Update to {upd.update.version}
            </ButtonItem>
          </PanelSectionRow>
        ) : (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={checking}
              description={
                upd.error
                  ? `✗ ${upd.error}`
                  : upd.checked
                    ? `Up to date${upd.installed ? ` (${upd.installed})` : ""}.`
                    : upd.installed
                      ? `Installed: ${upd.installed}`
                      : undefined
              }
              onClick={async () => {
                setChecking(true);
                await checkForUpdate({ quiet: true });
                setChecking(false);
              }}
            >
              {checking ? "Checking…" : "Check for updates"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ToggleField
            label="Check automatically"
            description="Looks for a new version on GitHub when Steam starts and twice a day."
            checked={settings.check_updates}
            onChange={async (v) => {
              setSettings(await updateSetting("check_updates", v));
              if (v) checkForUpdate({ quiet: true });
            }}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Save groups">
        {(groups?.groups ?? []).map((g) => (
          <PanelSectionRow key={g.id}>
            <Field
              label={g.label || g.id}
              description={
                (plans[g.id] ? summary(plans[g.id]) + " · " : "") +
                (g.appids.length ? `${g.appids.length} game(s) mapped` : "manual sync only")
              }
            />
          </PanelSectionRow>
        ))}
        {groups && groups.groups.length === 0 && (
          <PanelSectionRow>
            <Field description="No save groups yet. Find your EmuDeck saves below." />
          </PanelSectionRow>
        )}
        {groups?.remote && groups.groups.length > 0 && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={blocked}
              onClick={() =>
                task("Checking…", async () => {
                  const r = await memcard<GroupPlan[]>(["status"]);
                  if (r.error) return setError(r.error);
                  setPlans(Object.fromEntries((r.result ?? []).map((p) => [p.group, p])));
                })
              }
            >
              Check what would sync
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!!busy}
            onClick={() =>
              task("Looking for saves…", async () => {
                const r = await memcard<{ saves: string; new: string[]; appid_updates: Record<string, number[]> }>(["detect"]);
                if (r.error) return setError(r.error);
                const found = r.result!;
                const changes = found.new.length + Object.keys(found.appid_updates).length;
                if (!changes) return toast(`Nothing new in ${found.saves}.`);
                showModal(
                  <ConfirmModal
                    strTitle="Add these save groups?"
                    strDescription={
                      `Found in ${found.saves}:\n` +
                      [...found.new.map((id) => `• new group: ${id}`), ...Object.keys(found.appid_updates).map((id) => `• more games mapped to ${id}`)].join("\n")
                    }
                    strOKButtonText="Add"
                    onOK={() =>
                      task("Saving…", async () => {
                        const w = await memcard(["detect", "--write"]);
                        if (w.error) setError(w.error);
                        else toast("Save groups updated.");
                      })
                    }
                  />,
                );
              })
            }
          >
            Find EmuDeck saves
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Tools">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => showSetup(true)}>
            Change cloud storage
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!!busy}
            onClick={() =>
              task("Installing rclone…", async () => {
                const err = await installRclone();
                if (err) setError(err);
                else toast("rclone installed.");
              })
            }
          >
            Install or update rclone
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              const r = await memcard<{ path: string; lines?: string[] }>(["log", "-n", "60"]);
              showText("Memcard log", r.result?.lines?.join("\n") || r.result?.path || errorText(r.error));
            }}
          >
            Show log
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={reload}>
            Refresh
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  loadSettings();
  environment().then((env) => {
    if (env.configured) refreshGroups();
  });
  const unregister = registerLaunchHooks();
  const stopUpdateChecks = startUpdateChecks();
  return {
    name: "Memcard",
    titleView: <div className={staticClasses.Title}>Memcard</div>,
    content: <Content />,
    icon: <FaSdCard />,
    onDismount() {
      unregister();
      stopUpdateChecks();
    },
  };
});
