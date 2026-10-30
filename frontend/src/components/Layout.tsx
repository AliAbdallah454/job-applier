import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { isActive, useGenerations, useOptions } from "../api/queries";

const LINKS = [
  { to: "/", label: "Jobs" },
  { to: "/generations", label: "Tailored CVs" },
  { to: "/cvs", label: "CVs" },
  { to: "/profile", label: "Profile" },
];

export function Layout() {
  const { pathname } = useLocation();
  const options = useOptions();
  const generations = useGenerations().data ?? [];
  const working = generations.filter((item) => isActive(item.status)).length;
  const ready = generations.filter((item) => item.status === "ready").length;
  const nav = useRef<HTMLElement>(null);
  const [marker, setMarker] = useState<{ left: number; width: number } | null>(null);
  const clicks = useRef(0);
  const [rolling, setRolling] = useState(false);
  const logoClicked = () => {
    clicks.current += 1;
    if (clicks.current < 5) return;
    clicks.current = 0;
    setRolling(true);
  };

  useEffect(() => {
    const element = nav.current;
    if (!element) return;
    const measure = () => {
      const active = element.querySelector<HTMLElement>('[aria-current="page"]');
      setMarker(active && { left: active.offsetLeft, width: active.offsetWidth });
    };
    measure();
    // Tab widths change when the web font arrives, the window resizes or the count appears.
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [pathname, ready]);

  return (
    <div
      className={`flex min-h-dvh flex-col overflow-x-clip ${rolling ? "barrel-roll" : ""}`}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget) setRolling(false);
      }}
    >
      <header className="z-20 flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-rule bg-paper px-4 py-2.5 sm:px-6 md:sticky md:top-0 md:grid md:h-[53px] md:grid-cols-[1fr_auto_1fr] md:py-0">
        <Link
          to="/"
          aria-label="job applier"
          onClick={logoClicked}
          className="logo flex w-fit items-center gap-2 text-lg font-bold tracking-tight whitespace-pre"
        >
          <svg aria-hidden viewBox="0 0 16 20" className="logo-mark h-5 w-4 fill-ink">
            <path d="M0 0h10.5L16 5.5V20H0z" />
            <path d="M11.5 0 16 4.5h-4.5z" className="fill-paper" />
          </svg>
          <span>
            {[..."job applier"].map((letter, index) => (
              <span key={index} aria-hidden style={{ animationDelay: `${index * 35}ms` }}>
                {letter}
              </span>
            ))}
          </span>
        </Link>
        <nav
          ref={nav}
          className="relative order-last flex w-full border border-rule p-0.5 text-sm md:order-none md:w-auto"
        >
          {marker && (
            <span
              aria-hidden
              className="nav-marker absolute inset-y-0.5 left-0 bg-ink"
              style={{ width: marker.width, transform: `translateX(${marker.left}px)` }}
            />
          )}
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className="relative flex flex-1 items-center justify-center gap-1.5 px-2 py-1 font-medium whitespace-nowrap text-muted transition-colors duration-200 hover:text-ink aria-[current=page]:text-paper sm:px-3.5"
            >
              {link.label}
              {link.to === "/generations" && ready > 0 && (
                <span className="font-mono text-[10px] opacity-70">{ready}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <span className="ml-auto flex items-center gap-3 font-mono text-[11px] text-muted">
          {working > 0 && (
            <Link
              to="/generations"
              className="flex items-center gap-1.5 whitespace-nowrap text-ink hover:opacity-70"
            >
              <span className="pulse size-1.5 bg-ink" />
              tailoring {working}
            </Link>
          )}
          {options.data?.demo && (
            <span className="border border-dashed border-rule px-1.5 py-0.5 whitespace-nowrap">
              demo data
            </span>
          )}
        </span>
      </header>
      <main key={pathname} className="rise flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
