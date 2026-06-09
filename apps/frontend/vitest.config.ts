import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["components/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"],
      // Config / glue modules that only wire singletons and have no
      // meaningful branches to unit-test.
      exclude: ["lib/msal-config.ts", "components/auth/MsalProvider.tsx"],
      thresholds: {
        lines: 60,
        functions: 60,
        statements: 60,
        branches: 60,
      },
    },
  },
});
