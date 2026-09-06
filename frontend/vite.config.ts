import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import checker from "vite-plugin-checker"

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    checker({ typescript: true, enableBuild: false }), // `npm run build` already runs tsc -b
  ],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/scenes": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/kit": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
})
