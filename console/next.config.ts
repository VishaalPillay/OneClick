import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The console is screen-recorded for the submission video, so nothing may overlay the UI.
  devIndicators: false,

  // A self-contained server under .next/standalone for console/Dockerfile. Vercel ignores it.
  output: "standalone",

  turbopack: {
    // The repo is the root: the page is prerendered from files beside console/ (prompts, config.py,
    // eval sets, docs/metrics.md), and the standalone server for console/Dockerfile is laid out
    // relative to it (.next/standalone/console/server.js). Keep it if you move things around.
    root: path.join(__dirname, ".."),
  },
};

export default nextConfig;
