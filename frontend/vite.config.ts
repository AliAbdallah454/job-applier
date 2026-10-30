import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    allowedHosts: ["45df-80-12-41-71.ngrok-free.app"],
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: { environment: "jsdom", setupFiles: "./src/test/setup.ts", css: false },
});
