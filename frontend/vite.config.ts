import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api -> FastAPI on :8080, so the browser makes same-origin
// calls and we avoid CORS entirely in development. The prefix is forwarded as
// is: the backend serves the whole API under /api (see API_PREFIX in main.py),
// which is what lets the container serve this SPA from the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // VITE_API_TARGET points a second dev stack at another backend (a
        // review copy on another port); unset, it is the usual :8080.
        target: process.env.VITE_API_TARGET || "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
});
