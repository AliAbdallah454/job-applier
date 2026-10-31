import type { PDFDocumentLoadingTask, PDFPageProxy } from "pdfjs-dist";
import { useCallback, useEffect, useRef, useState } from "react";

import { ACTION, Skeleton } from "./ui";

export type PdfPosition = { page: number; x: number; y: number };

const ZOOMS = [0.75, 1, 1.25, 1.5, 2, 3];

/** The PDF text run under a position; its y is measured from the bottom of the page. */
async function textAt(page: PDFPageProxy, x: number, y: number) {
  const { items } = await page.getTextContent();
  const found = items.find(
    (item) =>
      "str" in item &&
      x >= item.transform[4] &&
      x <= item.transform[4] + item.width &&
      y >= item.transform[5] - item.height * 0.3 &&
      y <= item.transform[5] + item.height,
  );
  return found && "str" in found ? found.str : "";
}

/** Imported on first use, so only someone viewing a CV downloads the PDF library. */
async function loadPdfJs() {
  const [pdfjs, worker] = await Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
  ]);
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  return pdfjs;
}

function PdfPage({
  page,
  width,
  onLocate,
  onError,
}: {
  page: PDFPageProxy;
  width: number;
  onLocate: (position: PdfPosition, text: string) => void;
  onError: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const size = page.getViewport({ scale: 1 });

  useEffect(() => {
    if (!canvas.current || width === 0) return;
    const viewport = page.getViewport({
      scale: (width / size.width) * window.devicePixelRatio,
    });
    canvas.current.width = Math.floor(viewport.width);
    canvas.current.height = Math.floor(viewport.height);
    const task = page.render({ canvas: canvas.current, viewport });
    let cancelled = false;
    task.promise.catch(() => {
      if (!cancelled) onError();
    });
    return () => {
      cancelled = true;
      task.cancel();
    };
  }, [page, width, size.width, onError]);

  return (
    <canvas
      ref={canvas}
      aria-label={`Page ${page.pageNumber}`}
      style={{ width, height: (width / size.width) * size.height }}
      className="mx-auto block bg-white"
      onDoubleClick={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        const x = ((event.clientX - box.left) / box.width) * size.width;
        const y = ((event.clientY - box.top) / box.height) * size.height;
        void textAt(page, x, size.height - y).then((text) =>
          onLocate({ page: page.pageNumber, x, y }, text),
        );
      }}
    />
  );
}

/** Drawn here rather than in the browser's viewer, whose double-clicks a page cannot see. */
export function PdfPreview({
  title,
  url,
  onLocate,
}: {
  title: string;
  url: string;
  onLocate: (position: PdfPosition, text: string) => void;
}) {
  const [pages, setPages] = useState<PDFPageProxy[]>([]);
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const scroller = useRef<HTMLDivElement>(null);
  const showFailure = useCallback(() => setFailed(true), []);

  useEffect(() => {
    let stale = false;
    let task: PDFDocumentLoadingTask | undefined;
    loadPdfJs()
      .then(async (pdfjs) => {
        if (stale) return;
        task = pdfjs.getDocument(url);
        const pdf = await task.promise;
        const loaded = await Promise.all(
          Array.from({ length: pdf.numPages }, (_, index) => pdf.getPage(index + 1)),
        );
        // The old pages stay until now, so saving an edit keeps the scroll position.
        if (!stale) {
          setPages(loaded);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!stale) setFailed(true);
      });
    return () => {
      stale = true;
      void task?.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (!scroller.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry.contentRect.width)),
    );
    observer.observe(scroller.current);
    return () => observer.disconnect();
  }, []);

  const level = ZOOMS.indexOf(zoom);
  return (
    <div className="mt-3 flex h-[70dvh] min-h-0 flex-1 flex-col lg:h-auto">
      <div className="flex items-center justify-between gap-3 px-4 pb-2 sm:px-6">
        <p className="text-xs text-muted">Double-click the CV to find that line in the LaTeX.</p>
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={level === 0}
            onClick={() => setZoom(ZOOMS[level - 1])}
            className={`${ACTION} disabled:opacity-40`}
          >
            −
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={level === ZOOMS.length - 1}
            onClick={() => setZoom(ZOOMS[level + 1])}
            className={`${ACTION} disabled:opacity-40`}
          >
            +
          </button>
        </span>
      </div>
      <div
        ref={scroller}
        role="group"
        aria-label={title}
        className="min-h-0 flex-1 space-y-2 overflow-auto bg-wash p-2"
      >
        {failed && (
          <p role="alert" className="p-4 text-sm">
            The CV preview could not be shown. You can still{" "}
            <a href={url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
              open the PDF
            </a>
            .
          </p>
        )}
        {!failed && pages.length === 0 && <Skeleton rows={3} />}
        {!failed &&
          pages.map((page) => (
            <PdfPage
              key={page.pageNumber}
              page={page}
              width={Math.floor(width * zoom)}
              onLocate={onLocate}
              onError={showFailure}
            />
          ))}
      </div>
    </div>
  );
}
