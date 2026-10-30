import { describe, expect, it } from "vitest";
import { isActive, MENU, menuFor } from "./menu";

const items = (groups: string[]) => menuFor(groups).flatMap((s) => s.items.map((i) => i.label));
const item = (label: string) => MENU.flatMap((s) => s.items).find((i) => i.label === label)!;

describe("admin menu", () => {
  it("shows admins everything", () => {
    expect(items(["admin"])).toEqual(["Dashboard", "Tables"]);
  });
  it("hides admin-only items from support staff", () => {
    expect(items(["support"])).toEqual(["Dashboard"]);
  });
  it("shows nothing to other accounts", () => {
    expect(items(["youth"])).toEqual([]);
  });
  it("drops a section with nothing left in it", () => {
    expect(menuFor(["support"]).map((s) => s.title)).toEqual([null]);
  });

  it("highlights Dashboard only on its own page", () => {
    expect(isActive(item("Dashboard"), "/admin")).toBe(true);
    expect(isActive(item("Dashboard"), "/admin/tables")).toBe(false);
  });
  it("highlights Tables on the list and on each table's pages", () => {
    for (const path of ["/admin/tables", "/admin/identity.account_subject", "/admin/vault.document/abc", "/admin/planning.plan_answer/new"]) {
      expect(isActive(item("Tables"), path)).toBe(true);
    }
    expect(isActive(item("Tables"), "/admin")).toBe(false);
  });
});
