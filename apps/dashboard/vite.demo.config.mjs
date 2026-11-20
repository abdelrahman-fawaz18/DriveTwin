import { defineConfig } from "vite";
import path from "node:path";
export default defineConfig({
  base: "./",
  publicDir: "demo-public",
  server: { host: "127.0.0.1", port: 4177, strictPort: true },
  preview: { host: "127.0.0.1", port: 4177, strictPort: true },
  build: {
    outDir: "dist-demo",
    rolldownOptions: { input: { demo: path.resolve("demo.html") } },
  },
});
