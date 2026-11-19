// Shapes of `memcard --json` output that the plugin reads. Mirrors core's to_dict()s.

export interface MemcardError {
  code: string;
  message: string;
  hint?: string | null;
  detail?: string | null;
}

export interface RunResult<T = any> {
  exit_code: number | null;
  result: T | null;
  error: MemcardError | null;
  warnings: string[];
}

export interface FileEntry {
  sha256: string;
  size: number;
  mtime?: string;
  written_by?: string;
  written_at?: string;
}

export interface FileChange {
  path: string;
  verdict: "noop" | "push" | "pull" | "conflict";
  local: FileEntry | null;
  remote: FileEntry | null;
  base: FileEntry | null;
}

export interface UnitPlan {
  key: string;
  verdict: "noop" | "push" | "pull" | "conflict";
  reason: "local_wiped" | "remote_wiped" | null;
  files: FileChange[];
}

export interface GroupResult {
  group: string;
  status: "ok" | "conflicts" | "skipped" | "error";
  pushed: string[];
  pulled: string[];
  conflicts: UnitPlan[];
  machines: Record<string, string>;
  error: MemcardError | null;
}

export interface SyncData {
  results: GroupResult[];
  appid?: number;
  mapped?: boolean;
}

export interface GroupInfo {
  id: string;
  label: string;
  unit: string;
  path: string;
  emulator: string | null;
  appids: number[];
}

export interface GroupsData {
  machine: { id: string; name: string };
  remote: { spec: string; provider: string | null } | null;
  groups: GroupInfo[];
  pending: Record<string, { since: string; units: string[] }>;
}

export interface GroupPlan {
  group: string;
  remote_generation: number | null;
  summary: Record<"noop" | "push" | "pull" | "conflict", number>;
  units: UnitPlan[];
  error?: MemcardError;
}

export interface ProviderField {
  key: string;
  prompt: string;
  example: string;
  secret: boolean;
  help: string;
}

export interface Provider {
  key: string;
  label: string;
  oauth: boolean;
  experimental: boolean;
  manual_setup: string;
  doc: string;
  fields: ProviderField[];
}

export interface ExistingRemote {
  name: string;
  config_path: string;
  provider: string;
  label: string;
  experimental: boolean;
  owned: boolean;
}

export type Choice = "local" | "remote" | "both";

// `memcard history list GROUP --json`
export interface HistorySide {
  size: number;
  written_at: string | null;
  written_by: string | null;
  machine: string;
}

export interface HistoryChange {
  path: string;
  unit: string;
  change: "added" | "modified" | "deleted";
  size: number | null;
}

export interface HistoryUnitDiff {
  unit: string;
  restorable: boolean;
  files: { path: string; then: HistorySide | null; now: HistorySide | null }[];
}

export interface HistoryVersion {
  generation: number;
  committed_at: string;
  committed_by: string;
  machine: string;
  restored_from: number | null;
  current: boolean;
  changes: HistoryChange[] | null; // null: the version before it was cleaned up
  differs: HistoryUnitDiff[];
}

export type UnitKind = "group" | "file" | "dir";

export interface HistoryData {
  group: string;
  unit: UnitKind;
  head: number | null;
  machines: Record<string, string>;
  more: boolean;
  versions: HistoryVersion[];
}

// `memcard history restore … --json`
export interface RestoreData {
  results: GroupResult[];
  restored: { group: string; generation: number; restored_from: number; units: string[] | null } | null;
}
