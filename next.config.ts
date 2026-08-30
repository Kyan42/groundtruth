import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  agentRules: false,
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/", destination: "/landing.html" },
      { source: "/runs/:runId", destination: "/run.html" },
    ];
  },
};

export default nextConfig;
