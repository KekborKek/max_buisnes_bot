import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// В разработке запросы /api проксируются на бэкенд (порт из API_PORT, по умолчанию 8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    proxy: { "/api": `http://localhost:${process.env.API_PORT ?? 8000}` },
  },
});
