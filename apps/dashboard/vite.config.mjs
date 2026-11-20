import { defineConfig } from "vite";
import path from "node:path";
export default defineConfig({
  build: {
    rolldownOptions: {
      input: {
        monitoring: path.resolve("index.html"),
        replay: path.resolve("replay.html"),
      },
    },
  },
});
