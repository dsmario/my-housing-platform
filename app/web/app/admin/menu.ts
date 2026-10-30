// The admin side menu. Each item lists the staff groups that see it; the API
// checks the same groups on every request, so hiding an item is only tidiness.
// DRAFT wording, for review.

export type StaffGroup = "admin" | "support";

export type MenuItem = {
  label: string;
  href: string;
  groups: StaffGroup[];
  // Also highlighted on these pages (the table browser's own pages, for Tables).
  // A regex source string, not a RegExp: the menu is sent to a client component,
  // which only accepts plain data.
  match?: string;
};

export type MenuSection = { title: string | null; items: MenuItem[] };

export const MENU: MenuSection[] = [
  { title: null, items: [{ label: "Dashboard", href: "/admin", groups: ["admin", "support"] }] },
  { title: "Doc Vault", items: [{ label: "Vault health", href: "/admin/vault", groups: ["admin"] }] },
  {
    title: "System",
    items: [
      { label: "Rules & schemas", href: "/admin/rules", groups: ["admin"] },
      { label: "Tables", href: "/admin/tables", groups: ["admin"], match: "^/admin/[a-z_]+\\.[a-z_]+" },
    ],
  },
];

export function menuFor(groups: string[]): MenuSection[] {
  return MENU.map((s) => ({ ...s, items: s.items.filter((i) => i.groups.some((g) => groups.includes(g))) })).filter(
    (s) => s.items.length > 0,
  );
}

export function isActive(item: MenuItem, path: string): boolean {
  if (item.href === "/admin") return path === "/admin";
  return path === item.href || path.startsWith(`${item.href}/`) || (item.match ? new RegExp(item.match).test(path) : false);
}
