import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" makes the built index.html load its assets with relative paths,
// so the app also works when it is uploaded under a folder of the bucket.
export default defineConfig({
  plugins: [react()],
  base: "./",
});
