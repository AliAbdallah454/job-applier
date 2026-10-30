import { useEffect, useRef, useState } from "react";

export type Mood = "idle" | "working" | "happy" | "sad" | "sleepy";

const MOUTHS: Record<Mood, string> = {
  idle: "M28 47q5 3 10 0",
  working: "M31 48a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  happy: "M25 45q8 11 16 0z",
  sad: "M26 51q7-7 14 0",
  sleepy: "M30 48h6",
};
const JOKES = [
  "Measuring your inseam…",
  "Letting out the sleeves on your internship…",
  "Pinning the buzzwords in place…",
  "Hemming a gap in your timeline…",
  "Asking the scissors for a second opinion…",
  "Ironing out your soft skills…",
];
const CONFETTI = Array.from({ length: 44 }, (_, index) => index);

/** A sheet of paper with googly eyes that watch the pointer. */
export function Mascot({ mood = "idle", size = 72 }: { mood?: Mood; size?: number }) {
  const pupils = useRef<SVGGElement>(null);
  const watching = mood !== "sad" && mood !== "sleepy";

  useEffect(() => {
    if (!watching) return;
    const look = (event: PointerEvent) => {
      const element = pupils.current;
      if (!element) return;
      const box = element.getBoundingClientRect();
      const x = event.clientX - (box.x + box.width / 2);
      const y = event.clientY - (box.y + box.height / 2);
      const distance = Math.hypot(x, y) || 1;
      element.style.transform = `translate(${(x / distance) * 2}px, ${(y / distance) * 2}px)`;
    };
    window.addEventListener("pointermove", look);
    return () => window.removeEventListener("pointermove", look);
  }, [watching]);

  return (
    <svg
      aria-hidden
      viewBox="0 0 66 80"
      width={size}
      height={(size * 80) / 66}
      className={`mascot mascot-${mood} overflow-visible text-ink`}
    >
      {mood === "sleepy" && (
        <text x="56" y="10" className="mascot-zzz fill-current font-mono text-[11px]">
          z
        </text>
      )}
      <g className="mascot-body">
        <path d="M24 66v9h-6M42 66v9h6" className="mascot-legs" />
        <path d="M10 4h32l14 14v50H10z" className="mascot-sheet" />
        <path d="M42 4v14h14" className="mascot-line" />
        {watching ? (
          <g className="mascot-eyes">
            <circle cx="25" cy="33" r="6" className="mascot-sheet" />
            <circle cx="41" cy="33" r="6" className="mascot-sheet" />
            <g ref={pupils} className="mascot-pupils">
              <circle cx="25" cy="33" r="2.5" className="fill-current" />
              <circle cx="41" cy="33" r="2.5" className="fill-current" />
            </g>
          </g>
        ) : mood === "sad" ? (
          <path d="M21 29l8 8M29 29l-8 8M37 29l8 8M45 29l-8 8" className="mascot-line" />
        ) : (
          <path d="M20 34q5 3 10 0M36 34q5 3 10 0" className="mascot-line" />
        )}
        <path d={MOUTHS[mood]} className="mascot-line" />
      </g>
    </svg>
  );
}

/** Tailoring, literally: scissors snipping along a dashed line. */
export function Snip() {
  return (
    <div aria-hidden className="relative mt-8 h-7 overflow-hidden">
      <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-muted" />
      <svg viewBox="0 0 34 24" className="snip-run absolute top-0 h-7 w-10 text-ink">
        <g className="snip-blade snip-top">
          <path d="M16 12 33 6" className="mascot-line" />
          <circle cx="7" cy="18" r="4.5" className="mascot-sheet" />
          <path d="M10.5 15.5 16 12" className="mascot-line" />
        </g>
        <g className="snip-blade snip-bottom">
          <path d="M16 12 33 18" className="mascot-line" />
          <circle cx="7" cy="6" r="4.5" className="mascot-sheet" />
          <path d="M10.5 8.5 16 12" className="mascot-line" />
        </g>
      </svg>
    </div>
  );
}

export function Joke() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setIndex((current) => (current + 1) % JOKES.length), 3200);
    return () => clearInterval(timer);
  }, []);
  return (
    <p key={index} aria-hidden className="rise mt-3 font-mono text-[11px] text-muted">
      {JOKES[index]}
    </p>
  );
}

/** Paper scraps and a proud mascot, for the moment a run finishes while you watch. */
export function Celebration({ onDone }: { onDone: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDone, 4200);
    return () => clearTimeout(timer);
  }, [onDone]);
  return (
    <div aria-hidden className="celebration pointer-events-none fixed inset-0 z-50 overflow-hidden">
      {CONFETTI.map((index) => (
        <i
          key={index}
          className={index % 5 === 0 ? "scrap scrap-cv" : "scrap"}
          style={
            {
              left: `${(index * 37) % 100}%`,
              "--delay": `${(index * 53) % 900}ms`,
              "--fall": `${1600 + ((index * 71) % 1400)}ms`,
              "--spin": `${180 + ((index * 97) % 720)}deg`,
              "--drift": `${((index * 29) % 120) - 60}px`,
            } as React.CSSProperties
          }
        >
          {index % 5 === 0 ? "CV" : ""}
        </i>
      ))}
      <div className="rise absolute bottom-6 left-6 flex items-end gap-3">
        <Mascot mood="happy" size={64} />
        <p className="border border-ink bg-paper px-3 py-1.5 text-sm font-medium">
          Fits you perfectly!
        </p>
      </div>
    </div>
  );
}
