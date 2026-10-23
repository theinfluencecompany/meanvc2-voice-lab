import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({ plugins: [react(), tailwindcss()], base: "./", build: { sourcemap: false, assetsInlineLimit: 0 }, server: { host: "127.0.0.1", port: 33330, strictPort: true } });
