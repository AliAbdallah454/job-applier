import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { isActive, useGenerations, useOptions } from "../api/queries";
import { TAB } from "./ui";

const LINKS = [
  { to: "/", label: "Jobs" },
  { to: "/generations", label: "Tailored CVs" },
  { to: "/cvs", label: "CVs" },
  { to: "/profile", label: "Profile" },
];

export function Layout() {
  const { pathname } = useLocation();
  const options = useOptions();
  const working = useGenerations().data?.filter((item) => isActive(item.status)).length ?? 0;
  return (
    <div className="flex min-h-dvh flex-col overflow-x-clip">
      <header className="z-20 flex flex-wrap items-baseline gap-x-8 gap-y-1 border-b border-ink bg-paper px-4 py-3 sm:px-6 md:sticky md:top-0">
        <Link to="/" className="text-lg font-bold tracking-tight whitespace-nowrap">
          job applier
        </Link>
        <nav className="flex gap-4 text-sm sm:gap-5">
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.to === "/"} className={TAB}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <span className="ml-auto flex items-baseline gap-4 font-mono text-[11px] text-muted">
          {working > 0 && (
            <Link to="/generations" className="flex items-center gap-1.5 hover:text-ink">
              <span className="pulse size-1.5 bg-ink" />
              tailoring {working}
            </Link>
          )}
          {options.data?.demo && <span>demo data</span>}
        </span>
      </header>
      <main key={pathname} className="rise flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
