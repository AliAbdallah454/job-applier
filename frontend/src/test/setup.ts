import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

// jsdom has no dialog.close, and the app relies on the close event it fires.
HTMLDialogElement.prototype.close = function () {
  this.dispatchEvent(new Event("close"));
};
