import { openFilePicker, type FileSelectionType } from "@decky/api";
import { DialogButton, Focusable, ModalRoot, showModal, Spinner, TextField } from "@decky/ui";
import { useEffect, useState } from "react";
import { memcard, readTokenFile, refreshGroups } from "./backend";
import { errorText } from "./errorText";
import { DetailsButton } from "./textView";
import type { ExistingRemote, MemcardError, Provider } from "./types";

// Guided setup, one thing at a time. Every step is a `memcard setup …` call, so
// the checks (Nextcloud URL shape, write/read/delete round trip, error wording)
// are the same ones the terminal wizard uses. Secrets travel on stdin, never argv.

type Step =
  | { kind: "name" }
  | { kind: "choose" }
  | { kind: "existing"; remote: ExistingRemote }
  | { kind: "manual"; provider: Provider }
  | { kind: "field"; provider: Provider; index: number; values: Record<string, string> }
  | { kind: "connect"; provider: Provider; values: Record<string, string> }
  | { kind: "done"; spec: string; experimental: boolean };

const H = ({ children }: { children: React.ReactNode }) => (
  <div style={{ fontSize: "1.3em", fontWeight: "bold", marginBottom: 8 }}>{children}</div>
);
const P = ({ children, dim }: { children: React.ReactNode; dim?: boolean }) => (
  <div style={{ marginBottom: 8, opacity: dim ? 0.75 : 1, whiteSpace: "pre-wrap" }}>{children}</div>
);

function ErrorBox({ error, onInstallRclone }: { error: MemcardError | string | null; onInstallRclone?: () => void }) {
  if (!error) return null;
  const text = typeof error === "string" ? error : errorText(error);
  const missingRclone = typeof error !== "string" && error.code === "tool_missing" && error.message.includes("rclone");
  return (
    <div style={{ margin: "8px 0", padding: 8, borderRadius: 4, background: "rgba(255,80,80,0.15)" }}>
      ✗ {text}
      {typeof error !== "string" && <DetailsButton error={error} />}
      {missingRclone && onInstallRclone && (
        <DialogButton style={{ marginTop: 8 }} onClick={onInstallRclone}>
          Install rclone
        </DialogButton>
      )}
    </div>
  );
}

export async function installRclone(): Promise<MemcardError | null> {
  const r = await memcard(["setup", "install-rclone"]);
  return r.error;
}

function SetupModal({ configured, closeModal }: { configured: boolean; closeModal?: () => void }) {
  const [step, setStep] = useState<Step>(configured ? { kind: "choose" } : { kind: "name" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MemcardError | string | null>(null);
  const [text, setText] = useState("");
  const [folder, setFolder] = useState("memcard");
  const [remoteName, setRemoteName] = useState("");
  const [update, setUpdate] = useState(false);
  const [remotes, setRemotes] = useState<ExistingRemote[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);

  const go = (next: Step, value = "") => {
    setError(null);
    setText(value);
    setStep(next);
  };

  async function call<T>(fn: () => Promise<{ result: T | null; error: MemcardError | null }>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (r.error) setError(r.error);
      return r.error ? null : r.result;
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (step.kind !== "choose") return;
    (async () => {
      const [rem, prov] = await Promise.all([memcard<ExistingRemote[]>(["setup", "remotes"]), memcard<Provider[]>(["setup", "providers"])]);
      setRemotes(rem.result ?? []);
      setProviders(prov.result ?? []);
      if (rem.error || prov.error) setError(rem.error ?? prov.error);
    })();
  }, [step.kind]);

  const rcloneFix = async () => {
    setBusy(true);
    const err = await installRclone();
    setBusy(false);
    setError(err ?? "rclone installed. Try again.");
  };

  let body: React.ReactNode;
  switch (step.kind) {
    case "name":
      body = (
        <>
          <H>Name this machine</H>
          <P dim>Your other machines show this name in conflict prompts ("last written by …").</P>
          <TextField label="Machine name" value={text || ""} placeholder="e.g. Steam Machine, Steam Deck, Desktop" onChange={(e) => setText(e.target.value)} />
          <DialogButton
            disabled={busy}
            style={{ marginTop: 10 }}
            onClick={async () => {
              const ok = await call(() => memcard(["init", "--name", text.trim() || "Steam Deck"]));
              if (!ok) return;
              refreshGroups(); // the config exists now: the panel behind leaves "Set up Memcard" even if setup is abandoned
              go({ kind: "choose" });
            }}
          >
            Next
          </DialogButton>
        </>
      );
      break;

    case "choose":
      body = (
        <>
          <H>Where should your saves live?</H>
          {remotes.length > 0 && <P dim>Use a remote you already set up with rclone:</P>}
          <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {remotes.map((r) => (
              <DialogButton key={`${r.config_path}:${r.name}`} onClick={() => go({ kind: "existing", remote: r })}>
                Use "{r.name}" ({r.label}
                {r.experimental ? ", experimental" : ""})
              </DialogButton>
            ))}
            {remotes.length > 0 && <P dim>Or set up a new one:</P>}
            {providers.map((p) => (
              <DialogButton
                key={p.key}
                onClick={() => {
                  setRemoteName(p.key);
                  if (p.manual_setup) go({ kind: "manual", provider: p });
                  else go({ kind: "field", provider: p, index: 0, values: {} });
                }}
              >
                {p.label}
                {p.experimental ? " (experimental)" : ""}
              </DialogButton>
            ))}
            {!providers.length && !error && <Spinner style={{ width: 32 }} />}
          </Focusable>
        </>
      );
      break;

    case "existing":
      body = (
        <>
          <H>Use "{step.remote.name}"</H>
          {step.remote.experimental && <P dim>{step.remote.label} support is experimental. See docs/providers.</P>}
          <TextField label="Folder on the remote for Memcard" value={folder} onChange={(e) => setFolder(e.target.value)} />
          <DialogButton
            disabled={busy}
            style={{ marginTop: 10 }}
            onClick={async () => {
              const args = ["setup", "use", step.remote.name, "--folder", folder.trim() || "memcard"];
              if (!step.remote.owned) args.push("--rclone-config", step.remote.config_path);
              const r = await call(() => memcard<{ remote: string }>(args));
              if (r) go({ kind: "done", spec: r.remote, experimental: step.remote.experimental });
            }}
          >
            {busy ? "Testing (write, read back, delete)…" : "Connect"}
          </DialogButton>
        </>
      );
      break;

    case "manual":
      body = (
        <>
          <H>{step.provider.label}</H>
          {step.provider.experimental && <P>⚠ Experimental. Read {step.provider.doc} before relying on it.</P>}
          <P>{step.provider.manual_setup}</P>
          <DialogButton onClick={closeModal}>OK</DialogButton>
        </>
      );
      break;

    case "field": {
      const f = step.provider.fields[step.index];
      const isToken = f.key === "token";
      body = (
        <>
          <H>
            {step.provider.label}: {f.prompt}
          </H>
          {step.index === 0 && step.provider.oauth && (
            <P>
              {step.provider.label} signs in through a web browser, and Game Mode doesn't have one. On any computer with a browser,
              install rclone and run:{"\n\n"}    rclone authorize "{step.provider.key === "drive" ? "drive" : step.provider.key}"{"\n\n"}
              Then paste the token here, or save it to a file on an SD card or USB stick and load it.
            </P>
          )}
          {f.help && <P dim>{f.help}</P>}
          <TextField
            label={f.prompt}
            value={text}
            placeholder={f.secret ? "" : f.example}
            bIsPassword={f.secret && !isToken}
            onChange={(e) => setText(e.target.value)}
          />
          {f.example && !f.secret && <P dim>Example: {f.example}</P>}
          <Focusable style={{ display: "flex", gap: 8, marginTop: 10 }} flow-children="horizontal">
            {isToken && (
              <DialogButton
                onClick={async () => {
                  try {
                    const picked = await openFilePicker(0 as FileSelectionType, "/run/media", true, false);
                    setText(await readTokenFile(picked.path));
                  } catch (e) {
                    setError(String(e));
                  }
                }}
              >
                Load from file…
              </DialogButton>
            )}
            <DialogButton
              disabled={busy || !text.trim()}
              onClick={async () => {
                const values = { ...step.values, [f.key]: text.trim() };
                const r = await call(() =>
                  memcard<{ problems: Record<string, string> }>(["setup", "validate", "--provider", step.provider.key], { stdin: values }),
                );
                const problem = r?.problems?.[f.key];
                if (r === null && !problem) return; // call() already showed the error
                if (problem) return setError(problem);
                const next = step.provider.fields[step.index + 1];
                if (next) go({ ...step, index: step.index + 1, values }, values[next.key] ?? "");
                else go({ kind: "connect", provider: step.provider, values });
              }}
            >
              Next
            </DialogButton>
          </Focusable>
        </>
      );
      break;
    }

    case "connect":
      body = (
        <>
          <H>Connect {step.provider.label}</H>
          <TextField
            label="Name for this remote"
            value={remoteName}
            onChange={(e) => {
              setRemoteName(e.target.value);
              setUpdate(false); // "update" only ever means the remote that was found to exist
            }}
          />
          <TextField label="Folder on the remote for Memcard" value={folder} onChange={(e) => setFolder(e.target.value)} />
          {update && <P dim>Will update the existing remote "{remoteName}" in place.</P>}
          <DialogButton
            disabled={busy || !remoteName.trim()}
            style={{ marginTop: 10 }}
            onClick={async () => {
              const req = { provider: step.provider.key, name: remoteName.trim(), folder: folder.trim() || "memcard", update, values: step.values };
              setBusy(true);
              setError(null);
              const r = await memcard<{ remote: string }>(["setup", "apply"], { stdin: req });
              setBusy(false);
              if (r.result) return go({ kind: "done", spec: r.result.remote, experimental: step.provider.experimental });
              setError(r.error);
              if (r.error?.code === "remote_exists") setUpdate(true);
            }}
          >
            {busy ? "Testing (write, read back, delete)…" : update ? "Update and connect" : "Connect"}
          </DialogButton>
          {!busy && error && (
            <DialogButton
              style={{ marginTop: 6 }}
              onClick={() => go({ kind: "field", provider: step.provider, index: 0, values: step.values }, step.values[step.provider.fields[0].key] ?? "")}
            >
              Edit the settings
            </DialogButton>
          )}
        </>
      );
      break;

    case "done":
      body = (
        <>
          <H>Connected</H>
          <P>
            Memcard will keep your saves in {step.spec}.{step.experimental ? " This provider is experimental." : ""}
          </P>
          <P dim>Next, find your saves: use "Find EmuDeck saves" in the Memcard panel.</P>
          <DialogButton
            onClick={() => {
              refreshGroups();
              closeModal?.();
            }}
          >
            Done
          </DialogButton>
        </>
      );
      break;
  }

  return (
    <ModalRoot onCancel={closeModal} bAllowFullSize>
      {body}
      <ErrorBox error={error} onInstallRclone={rcloneFix} />
      {busy && step.kind !== "connect" && step.kind !== "existing" && <Spinner style={{ width: 24, marginTop: 6 }} />}
    </ModalRoot>
  );
}

export function showSetup(configured: boolean) {
  showModal(<SetupModal configured={configured} />);
}
