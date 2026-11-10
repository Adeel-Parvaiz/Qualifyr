import type { NextConfig } from "next";

const backendUrl = (process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const tunnelHost = process.env.NGROK_DOMAIN?.trim();

const nextConfig: NextConfig = {
  allowedDevOrigins: tunnelHost ? [tunnelHost] : [],
  async rewrites() {
    // Vercel routes /api to the Python function via vercel.json.
    if (process.env.VERCEL) return [];
    return [
      { source: "/api/:path*", destination: `${backendUrl}/:path*` },
    ];
  },
};

export default nextConfig;
