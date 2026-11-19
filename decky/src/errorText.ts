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

/** Text split for the viewer: one entry per line, without the trailing blank ones. */
export function textLines(text: string): string[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines;
}
