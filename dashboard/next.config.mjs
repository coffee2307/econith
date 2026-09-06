/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the production Docker image small.
  output: "standalone",
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
        destination: "http://localhost:8000/api/v1/:path*",
      },
      {
        source: "/api/social/:path*",
        destination: "http://localhost:5001/api/:path*",
      },
    ];
  },
};

export default nextConfig;
