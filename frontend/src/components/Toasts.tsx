import { useEffect, useRef, useState } from "react";

import { onNotice } from "../api/notices";

// The .toast fade out in index.css ends at the same moment.
const LIFETIME = 3500;
const MOST_SHOWN = 3;

export function Toasts() {
  const [notices, setNotices] = useState<{ id: number; message: string }[]>([]);
  const nextId = useRef(0);

  useEffect(
    () =>
      onNotice((message) => {
        const id = nextId.current++;
        setNotices((shown) => [...shown, { id, message }].slice(-MOST_SHOWN));
        setTimeout(() => {
          setNotices((shown) => shown.filter((notice) => notice.id !== id));
        }, LIFETIME);
      }),
    [],
  );

  return (
    <div
      role="status"
      className="pointer-events-none fixed top-4 right-4 z-40 flex flex-col items-end gap-2 md:top-[65px]"
    >
      {notices.map((notice) => (
        <p
          key={notice.id}
          className="toast flex items-center gap-2 border border-green bg-paper px-3 py-2 text-sm font-medium"
        >
          <svg
            aria-hidden
            viewBox="0 0 12 12"
            className="size-3 shrink-0 fill-none stroke-green stroke-[1.6]"
          >
            <path d="M1.5 6.5 4.5 9.5 10.5 2.5" />
          </svg>
          {notice.message}
        </p>
      ))}
    </div>
  );
}
