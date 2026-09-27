import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3002",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "./.venv/bin/python -m e2e.fixture_server",
      url: "http://127.0.0.1:8042/api/config",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "VITE_ENGINE_ORIGIN=http://127.0.0.1:8042 ./node_modules/.bin/vite dev --host 127.0.0.1 --port 3002 --strictPort",
      url: "http://127.0.0.1:3002",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
