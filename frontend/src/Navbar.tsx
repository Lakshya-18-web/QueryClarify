import { useState } from "react";

import {
  Cloud,
  Database,
  FolderUp,
  History,
  Library,
  Menu,
  Terminal,
  X,
} from "lucide-react";

import type { LucideIcon } from "lucide-react";

export type Page = "databases" | "query" | "library" | "cloud" | "history";

type NavbarProps = {
  active: Page;
  activeDatabase: string;
  onNavigate: (page: Page) => void;
};

const items: { id: Page; label: string; icon: LucideIcon }[] = [
  { id: "databases", label: "My Databases", icon: FolderUp },
  { id: "query", label: "Query Workspace", icon: Terminal },
  { id: "library", label: "Database Library", icon: Library },
  { id: "cloud", label: "Cloud Watch", icon: Cloud },
  { id: "history", label: "Query History", icon: History },
];

export default function Navbar({
  active,
  activeDatabase,
  onNavigate,
}: NavbarProps) {
  const [open, setOpen] = useState(false);

  const go = (page: Page) => {
    onNavigate(page);
    setOpen(false);
  };

  return (
    <header className="navbar">
      <div className="navbar-inner">
        <button className="navbar-brand" onClick={() => go("databases")}>
          <img src="/queryclarify-logo.png" alt="QueryClarify" />
          <span>
            Query<em>Clarify</em>
          </span>
        </button>

        <nav className={open ? "navbar-links open" : "navbar-links"}>
          {items.map((item) => {
            const Icon = item.icon;

            return (
              <button
                key={item.id}
                className={
                  active === item.id ? "nav-link active" : "nav-link"
                }
                onClick={() => go(item.id)}
              >
                <Icon size={16} />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="navbar-right">
          {activeDatabase && (
            <button className="navbar-db" onClick={() => go("query")}>
              <Database size={13} />
              <span>{activeDatabase}</span>
            </button>
          )}

          <button
            className="navbar-toggle"
            aria-label="Toggle navigation"
            onClick={() => setOpen(!open)}
          >
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>
    </header>
  );
}