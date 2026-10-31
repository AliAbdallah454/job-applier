import { useEffect, useState } from "react";

const root = document.documentElement;

const systemDark = () => window.matchMedia("(prefers-color-scheme: dark)");
const currentTheme = () => root.dataset.theme ?? (systemDark().matches ? "dark" : "light");

export function ThemeToggle() {
  const [theme, setTheme] = useState(currentTheme);
  const next = theme === "dark" ? "light" : "dark";

  useEffect(() => {
    const media = systemDark();
    const follow = () => setTheme(currentTheme());
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, []);

  const toggle = () => {
    root.dataset.theme = next;
    setTheme(next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      // Blocked storage only means the choice lasts until the next reload.
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      className="theme-toggle grid size-7 shrink-0 place-items-center border border-rule text-muted transition-colors duration-200 hover:border-ink hover:text-ink"
    >
      <svg
        key={theme}
        aria-hidden
        viewBox="0 0 16 16"
        className="size-3.5 fill-none stroke-current stroke-[1.5]"
      >
        {theme === "dark" ? (
          <>
            <circle cx="8" cy="8" r="3" />
            <path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1" />
          </>
        ) : (
          <path d="M13.5 9.8A6 6 0 0 1 6.2 2.5a6 6 0 1 0 7.3 7.3Z" />
        )}
      </svg>
    </button>
  );
}
