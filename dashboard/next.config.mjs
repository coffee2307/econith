/** @type {import('next').NextConfig} */
const backendInternalUrl =
  process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
const socialInternalUrl =
  process.env.SOCIAL_INTERNAL_URL ?? "http://127.0.0.1:5001";

const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the production Docker image small.
  output: "standalone",
  // This repository intentionally has a root lockfile and a dashboard
  // lockfile. Pin tracing to the dashboard so production builds are stable.
  outputFileTracingRoot: process.cwd(),
  turbopack: {
    root: process.cwd(),
  },
  // Remove the bottom-left dev overlay / 'N' build-activity indicator.
  // NOTE: Next 16 removed the granular `devIndicators.buildActivity` flag; the
  // modern equivalent that fully hides the indicator is `devIndicators: false`.
  devIndicators: false,
  async rewrites() {
    // Dev proxy: route dashboard REST calls to ECONITH backend so UI can use
    // same-origin paths (/api/v1/*) without CORS/host drift issues.
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendInternalUrl}/api/v1/:path*`,
      },
      {
        source: "/api/social/:path*",
        destination: `${socialInternalUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
