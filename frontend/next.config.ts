import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
    rules: { "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" } },
  },
  // Production build is a static export that Python can serve same-origin.
  // Dev API access goes through scripts/dev.mjs, not a rewrite (a rewrite would bypass the backend's Host guard).
  ...(isDev ? {} : { output: "export" as const }),
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
