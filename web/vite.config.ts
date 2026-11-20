import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", xfwd: true },
      "/llms.txt": { target: "http://127.0.0.1:8000", xfwd: true },
      "/sdk": "http://127.0.0.1:8000",
      "/skill": { target: "http://127.0.0.1:8000", xfwd: true },
      "/docs": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
