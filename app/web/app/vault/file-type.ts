// The content type to declare for an upload. Browsers often leave it empty for
// HEIC photos, so the file's extension decides then.
const TYPE_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  heic: "image/heic",
};

export function fileType(file: { name: string; type: string }): string {
  if (file.type) return file.type === "image/heif" ? "image/heic" : file.type;
  return TYPE_BY_EXTENSION[file.name.split(".").pop()?.toLowerCase() ?? ""] ?? "";
}
