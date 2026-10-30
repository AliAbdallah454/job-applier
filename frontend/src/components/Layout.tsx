import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { useOptions } from "../api/queries";

const LINKS = [
  { to: "/", label: "Jobs" },
  { to: "/generations", label: "Generations" },
  { to: "/cvs", label: "CVs" },
  { to: "/profile", label: "Profile" },
];

export function Layout() {
  const { pathname } = useLocation();
  const options = useOptions();
  return (
    <div className="flex min-h-dvh flex-col overflow-x-clip">
      <header className="flex flex-wrap items-baseline gap-x-8 gap-y-1 border-b border-ink px-4 py-3 sm:px-6">
        <Link to="/" className="text-lg font-bold tracking-tight whitespace-nowrap">
          job applier
        </Link>
        <nav className="flex gap-4 text-sm sm:gap-5">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                isActive ? "font-semibold underline underline-offset-4" : "text-muted hover:text-ink"
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        {options.data?.demo && (
          <span className="ml-auto font-mono text-[11px] text-muted">demo data</span>
        )}
      </header>
      <main key={pathname} className="rise flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
