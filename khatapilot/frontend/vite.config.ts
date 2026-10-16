import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The browser never sees the owner token: it calls /api on this dev server, and the proxy adds
// "Authorization: Bearer <OWNER_TOKEN>" from the repo's .env on the way to the backend.
export default defineConfig(({ mode }) => {
  const repoRoot = fileURLToPath(new URL("..", import.meta.url));
  const env = { ...loadEnv(mode, repoRoot, ""), ...process.env };
  const backend = env.BACKEND_URL || "http://127.0.0.1:8000";
  const token = env.OWNER_TOKEN || "";
  const proxy = {
    "/api": {
      target: backend,
      changeOrigin: true,
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    },
  };
  return {
    plugins: [react(), tailwindcss()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, proxy },
  };
});
