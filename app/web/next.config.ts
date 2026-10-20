import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server build for the container image.
  output: "standalone",
};

export default nextConfig;
