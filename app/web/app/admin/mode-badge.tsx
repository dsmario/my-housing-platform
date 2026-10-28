import type { Mode } from "../../lib/admin-api";

// Draft wording for review.
const LABELS: Record<Mode, string> = {
  edit: "Can edit",
  read: "View only",
  summary: "Totals only",
};

export function ModeBadge({ mode }: { mode: Mode }) {
  return <span className={`badge badge-${mode}`}>{LABELS[mode]}</span>;
}
