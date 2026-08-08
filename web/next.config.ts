import type { NextConfig } from "next";

// The Python engine (Parakeet + llama-server) stays the backend; Next is only the face.
// Proxying /api keeps the browser on ONE origin, so there is no CORS to configure and no
// second URL to remember — and the engine keeps binding 127.0.0.1 only.
const nextConfig: NextConfig = {
  // WHY THIS EXISTS: Next 16 treats a request whose Host differs from the bind address as
  // cross-origin in dev and refuses the HMR WebSocket — so opening localhost:3210 while
  // the server binds 127.0.0.1 silently kills hot reload. The page then serves stale
  // code, edits appear not to apply, and you debug a bug you already fixed. Listing both
  // spellings of "this machine" restores it.
  allowedDevOrigins: ["127.0.0.1", "localhost", "127.0.0.1:3210", "localhost:3210"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8765/api/:path*" }];
  },
};
export default nextConfig;
