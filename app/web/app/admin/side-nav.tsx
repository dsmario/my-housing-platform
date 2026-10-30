"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { isActive, type MenuSection } from "./menu";

// Left menu, WordPress style. On narrow screens it folds behind a Menu button.
export function SideNav({ sections }: { sections: MenuSection[] }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]); // close after choosing a page on a phone

  return (
    <nav className="admin-nav" aria-label="Admin menu">
      <button type="button" className="admin-nav-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        ☰ Menu
      </button>
      <div className={open ? "admin-nav-items open" : "admin-nav-items"}>
        {sections.map((section) => (
          <div key={section.title ?? "main"} className="admin-nav-section">
            {section.title && <div className="admin-nav-title">{section.title}</div>}
            <ul>
              {section.items.map((item) => {
                const active = isActive(item, path);
                return (
                  <li key={item.href}>
                    <Link href={item.href} className={active ? "active" : undefined} aria-current={active ? "page" : undefined}>
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}
