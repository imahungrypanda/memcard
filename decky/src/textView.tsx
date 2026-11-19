import { ButtonItem, DialogButton, Focusable, ModalRoot, showModal } from "@decky/ui";
import { errorDetail, errorText, textLines, type ErrorLike } from "./errorText";

// A read-only text viewer (the log, an error's raw output). A plain scroll box
// can't be scrolled with the D-pad, so every line is its own focus stop: moving
// down a line scrolls it into view.

export function showText(title: string, text: string, lead?: string): void {
  const lines = textLines(text);
  showModal(
    <ModalRoot bAllowFullSize>
      <div style={{ fontSize: "1.2em", fontWeight: "bold", marginBottom: 8 }}>{title}</div>
      {lead && <div style={{ marginBottom: 8, whiteSpace: "pre-wrap" }}>{lead}</div>}
      <Focusable style={{ maxHeight: 400, overflowY: "auto", fontFamily: "monospace", fontSize: "0.75em" }} flow-children="vertical">
        {lines.map((line, i) => (
          <Focusable key={i} onActivate={() => {}} style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", padding: "1px 4px" }}>
            {line || " "}
          </Focusable>
        ))}
      </Focusable>
    </ModalRoot>,
  );
}

export function showErrorDetails(e: ErrorLike): void {
  showText("Error details", errorDetail(e) ?? "", `✗ ${errorText(e)}`);
}

/** "Show details" under an error, only when it has raw output. `panel` for the side panel, otherwise inside a dialog. */
export function DetailsButton({ error, panel }: { error: ErrorLike | null | undefined; panel?: boolean }) {
  if (!error || !errorDetail(error)) return null;
  const open = () => showErrorDetails(error);
  return panel ? (
    <ButtonItem layout="below" onClick={open}>
      Show details
    </ButtonItem>
  ) : (
    <DialogButton style={{ marginTop: 6 }} onClick={open}>
      Show details
    </DialogButton>
  );
}
