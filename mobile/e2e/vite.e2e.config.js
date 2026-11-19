// Test-only Vite config. Never import in production or Pages workflows.
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: {
      "@clerk/clerk-js": fileURLToPath(new URL("./clerkMock.js", import.meta.url))
    }
  },
  server: { host: "127.0.0.1", port: 4173, strictPort: true }
});
