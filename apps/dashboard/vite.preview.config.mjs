import { defineConfig } from "vite";
import path from "node:path";

export default defineConfig({
  server: { host: "127.0.0.1", port: 4175, strictPort: true, open: false },
  build: {
    outDir: "dist-preview",
    rolldownOptions: { input: path.resolve("preview.html") },
  },
});
