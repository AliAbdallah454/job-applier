import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PdfPreview } from "../components/PdfPreview";

const getDocument = vi.hoisted(() => vi.fn());

vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: {}, getDocument }));
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({ default: "/worker.js" }));

function loadDocument() {
  const page = {
    pageNumber: 2,
    getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
    render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
    getTextContent: async () => ({
      items: [
        { str: "Elsewhere", transform: [1, 0, 0, 1, 0, 700], width: 100, height: 10 },
        { str: "Built an API", transform: [1, 0, 0, 1, 250, 598], width: 100, height: 10 },
      ],
    }),
  };
  getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 1, getPage: async () => page }),
    destroy: vi.fn(),
  });
  return page;
}

describe("PdfPreview", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private notify: (entries: { contentRect: { width: number } }[]) => void) {}
        observe() {
          this.notify([{ contentRect: { width: 300 } }]);
        }
        disconnect() {}
      },
    );
  });

  it("reports a double-click in points from the top left of the page", async () => {
    loadDocument();
    const onLocate = vi.fn();
    render(<PdfPreview title="CV preview" url="/cv.pdf" onLocate={onLocate} />);

    const canvas = await screen.findByLabelText("Page 2");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      width: 300,
      height: 400,
    } as DOMRect);
    fireEvent.doubleClick(canvas, { clientX: 160, clientY: 120 });

    await waitFor(() =>
      expect(onLocate).toHaveBeenCalledWith({ page: 2, x: 300, y: 200 }, "Built an API"),
    );
  });

  it("gives the canvas enough pixels for the size it is shown at", async () => {
    loadDocument();
    render(<PdfPreview title="CV preview" url="/cv.pdf" onLocate={vi.fn()} />);

    const canvas = (await screen.findByLabelText("Page 2")) as HTMLCanvasElement;
    expect([canvas.width, canvas.height]).toEqual([300, 400]);
  });

  it("draws larger when zoomed in", async () => {
    const page = loadDocument();
    render(<PdfPreview title="CV preview" url="/cv.pdf" onLocate={vi.fn()} />);
    const canvas = await screen.findByLabelText("Page 2");
    expect(canvas).toHaveStyle({ width: "300px", height: "400px" });

    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));

    expect(canvas).toHaveStyle({ width: "375px", height: "500px" });
    expect(page.render).toHaveBeenCalledTimes(2);
  });

  it("offers the PDF itself when it cannot be drawn", async () => {
    getDocument.mockReturnValue({
      promise: Promise.reject(new Error("broken")),
      destroy: vi.fn(),
    });
    render(<PdfPreview title="CV preview" url="/cv.pdf" onLocate={vi.fn()} />);

    expect(await screen.findByRole("link", { name: "open the PDF" })).toHaveAttribute(
      "href",
      "/cv.pdf",
    );
  });
});
