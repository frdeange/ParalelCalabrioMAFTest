import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) so the Docker
  // runtime image only needs Node + the traced dependencies, not the full
  // node_modules tree. Required by apps/frontend/Dockerfile.
  output: "standalone",
};

export default nextConfig;
