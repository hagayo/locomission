import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 390, height: 844 },
    baseURL: "http://127.0.0.1:4173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    geolocation: { latitude: 32.0853, longitude: 34.7818, accuracy: 12 },
    permissions: ["geolocation"]
  },
  webServer: {
    command: "npx vite --config e2e/vite.e2e.config.js --host 127.0.0.1 --port 4173 --strictPort",
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      VITE_CLERK_PUBLISHABLE_KEY: "pk_test_dGVzdC5jbGVyay5sb2NhbCQ=",
      VITE_API_BASE_URL: ""
    }
  }
});
