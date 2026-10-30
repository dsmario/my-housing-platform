import { describe, expect, it } from "vitest";
import { fileType } from "./file-type";
import { categoryLabel, CATEGORIES, DOWNLOAD_ERRORS, MAX_BYTES, size, STATUS } from "./labels";

describe("fileType: the type declared for an upload", () => {
  it("uses the browser's type when there is one", () => {
    expect(fileType({ name: "scan.pdf", type: "application/pdf" })).toBe("application/pdf");
  });
  it("treats HEIF as HEIC, which the API accepts", () => {
    expect(fileType({ name: "IMG_0001.HEIF", type: "image/heif" })).toBe("image/heic");
  });
  it("falls back to the extension when the browser leaves it empty (common for HEIC)", () => {
    expect(fileType({ name: "IMG_0001.HEIC", type: "" })).toBe("image/heic");
    expect(fileType({ name: "photo.JPEG", type: "" })).toBe("image/jpeg");
  });
  it("gives nothing for unknown files, so the API refuses them", () => {
    expect(fileType({ name: "notes.txt", type: "" })).toBe("");
    expect(fileType({ name: "no-extension", type: "" })).toBe("");
  });
});

describe("labels shown to youth", () => {
  it("matches the API's categories and limits", () => {
    expect(CATEGORIES.map(([key]) => key)).toEqual(["id_documents", "income", "housing", "other"]);
    expect(MAX_BYTES).toBe(15 * 1024 * 1024);
  });
  it("names categories, with Other for anything unknown", () => {
    expect(categoryLabel("id_documents")).toBe("ID");
    expect(categoryLabel("something-new")).toBe("Other");
  });
  it("shows sizes in KB under 1 MB and MB above", () => {
    expect(size(10)).toBe("1 KB");
    expect(size(2048)).toBe("2 KB");
    expect(size(1.5 * 1024 * 1024)).toBe("1.5 MB");
  });
  it("has a word for every status a youth can see", () => {
    for (const status of ["uploading", "scanning", "ready", "rejected", "expired"]) expect(STATUS[status]).toBeTruthy();
  });
  it("explains each refusal the download can return", () => {
    for (const code of ["403", "409", "410"]) expect(DOWNLOAD_ERRORS[code]).toBeTruthy();
  });
});
