import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  serverExternalPackages: ["better-sqlite3", "googleapis", "pdf-parse"],
};

export default nextConfig;
