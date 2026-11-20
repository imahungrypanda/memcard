// Wording for the Save groups pop-up and the panel button that opens it. Pure, so Node can test it.
import type { GroupInfo, GroupPlan } from "./types";

/** What "Check what would sync" found for one group. "" before it has run. */
export function planSummary(plan: GroupPlan | undefined): string {
  if (!plan) return "";
  if (plan.error) return `✗ ${plan.error.message}`;
  const s = plan.summary;
  const parts = [s.push && `${s.push} to upload`, s.pull && `${s.pull} to download`, s.conflict && `${s.conflict} conflict(s)`].filter(Boolean);
  return parts.length ? parts.join(", ") : "✓ up to date";
}

/** The second line of a group's row in the pop-up. */
export function groupLine(group: GroupInfo, plan: GroupPlan | undefined): string {
  const mapped = group.appids.length ? `${group.appids.length} game(s) mapped` : "manual sync only";
  const found = planSummary(plan);
  return found ? `${found} · ${mapped}` : mapped;
}

/** The panel button's description: how many groups, and what a check found across all of them. */
export function groupsSummary(groups: GroupInfo[], plans: Record<string, GroupPlan>): string {
  const count = groups.length === 1 ? "1 group" : `${groups.length} groups`;
  const checked = groups.map((g) => plans[g.id]).filter((p): p is GroupPlan => !!p);
  if (!checked.length) return `${count}. Pick one to see and restore its older saves.`;
  const sum = (k: "push" | "pull" | "conflict") => checked.reduce((n, p) => n + (p.error ? 0 : p.summary[k]), 0);
  const failed = checked.filter((p) => p.error).length;
  const parts = [
    sum("push") && `${sum("push")} to upload`,
    sum("pull") && `${sum("pull")} to download`,
    sum("conflict") && `${sum("conflict")} conflict(s)`,
    failed && `${failed} couldn't be checked`,
  ].filter(Boolean);
  return `${count} · ${parts.length ? parts.join(", ") : "✓ up to date"}`;
}
