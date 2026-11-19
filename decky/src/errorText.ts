// Wording for core's errors (`message` + `hint`, raw tool output in `detail`).
// Pure, so Node can test it; textView.tsx renders it.

export interface ErrorLike {
  message: string;
  hint?: string | null;
  detail?: string | null;
}

export function errorText(e: ErrorLike | null | undefined): string {
  if (!e) return "";
  return e.hint ? `${e.message} ${e.hint}` : e.message;
}

/** The raw output behind an error, or null when there is none or it only repeats the summary. */
export function errorDetail(e: ErrorLike | null | undefined): string | null {
  const detail = e?.detail?.trim();
  if (!detail || detail === e!.message.trim() || detail === errorText(e).trim()) return null;
  return detail;
}

/**
 * The error to show for a status run: one with raw output if any group has it. A group whose
 * folder is missing fails before rclone runs, so its error has no detail to show.
 */
export function statusError<E extends ErrorLike>(plans: { error?: E | null }[]): E | null {
  return (plans.find((p) => errorDetail(p.error)) ?? plans.find((p) => p.error))?.error ?? null;
}

const CHUNK = 200;

/**
 * Text split for the viewer: one entry per line, without the trailing blank ones. Long lines are
 * cut into chunks, since each entry is one D-pad stop and a stop taller than the box can't be read.
 */
export function textLines(text: string): string[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines.flatMap((line) => (line.length <= CHUNK ? [line] : Array.from({ length: Math.ceil(line.length / CHUNK) }, (_, i) => line.slice(i * CHUNK, (i + 1) * CHUNK))));
}
