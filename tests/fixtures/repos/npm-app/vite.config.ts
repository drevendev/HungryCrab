import { defineConfig } from "vite";

export default defineConfig({
  base: "/crab-cove/",
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
    },
  },
});
