// Words youth see on the vault page. DRAFT wording, for review. Keep it short and
// plain: many readers use English as a second language.

export const MAX_BYTES = 15 * 1024 * 1024;

// Order shown in the upload form. Keys match VAULT_CATEGORIES in the API.
export const CATEGORIES: [string, string][] = [
  ["id_documents", "ID"],
  ["income", "Income"],
  ["housing", "Housing"],
  ["other", "Other"],
];

export function categoryLabel(key: string): string {
  return CATEGORIES.find(([k]) => k === key)?.[1] ?? "Other";
}

export const STATUS: Record<string, string> = {
  uploading: "Checking…",
  scanning: "Checking…",
  ready: "Saved",
  rejected: "Not accepted",
};

// Shown after a download fails, by the status the API returned.
export const DOWNLOAD_ERRORS: Record<string, string> = {
  "403": "You can't open this document.",
  "409": "This document isn't ready yet.",
  "410": "This document can no longer be opened.",
};
export const DOWNLOAD_ERROR_OTHER = "Something went wrong. Try again later.";

export function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Sharing. DRAFT wording, for review.
export const SHARING = {
  shareLink: "Share",
  sectionTitle: "Who can see your documents",
  noShares: "You aren't sharing any documents.",
  stop: "Stop sharing",
  stopped: "Sharing stopped. They can't open it anymore.",
  shared: "Shared.",
  allIn: (category: string) => `All my ${category} documents`,
  laterNote: "This includes documents you add later.",
};
