import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/static/maintenance-v2/",
  build: {
    outDir: "../static/maintenance-v2",
    emptyOutDir: true,
    manifest: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": "http://127.0.0.1:5050",
      "/static": "http://127.0.0.1:5050",
      "/maintenance-docs": "http://127.0.0.1:5050",
      "/refactor-graph": "http://127.0.0.1:5050"
    }
  }
});
