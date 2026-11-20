import { DialogButton, Focusable, ModalRoot, showModal } from "@decky/ui";
import { ROW } from "./history";
import { groupLine } from "./groupsText";
import type { GroupInfo, GroupPlan } from "./types";

// The save groups, in a pop-up instead of a row each in the side panel: the panel is narrow and a
// long list there means scrolling past it to reach Tools. Picking a group opens its Cloud versions
// on top, so B comes back here.

interface Props {
  groups: GroupInfo[];
  plans: Record<string, GroupPlan>;
  open: (group: GroupInfo) => void;
  closeModal?: () => void;
}

function GroupsModal({ groups, plans, open, closeModal }: Props) {
  return (
    <ModalRoot onCancel={closeModal} bAllowFullSize>
      <div style={{ fontSize: "1.3em", fontWeight: "bold", marginBottom: 4 }}>Save groups</div>
      <div style={{ marginBottom: 10, opacity: 0.75 }}>Pick a group to see its cloud versions and restore older saves.</div>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 360, overflowY: "auto" }} flow-children="vertical">
        {groups.map((g) => (
          <DialogButton key={g.id} style={ROW} onClick={() => open(g)}>
            <div style={{ fontWeight: "bold" }}>{g.label || g.id}</div>
            <div style={{ fontSize: "0.8em", opacity: 0.8 }}>{groupLine(g, plans[g.id])}</div>
          </DialogButton>
        ))}
      </Focusable>
      <DialogButton style={{ marginTop: 10 }} onClick={closeModal}>
        Close
      </DialogButton>
    </ModalRoot>
  );
}

export function showGroups(groups: GroupInfo[], plans: Record<string, GroupPlan>, open: (group: GroupInfo) => void): void {
  showModal(<GroupsModal groups={groups} plans={plans} open={open} />);
}
