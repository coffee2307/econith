/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the production Docker image small.
  output: "standalone",
  // Remove the bottom-left dev overlay / 'N' build-activity indicator.
  // NOTE: Next 16 removed the granular `devIndicators.buildActivity` flag; the
  // modern equivalent that fully hides the indicator is `devIndicators: false`.
  devIndicators: false,
};

export default nextConfig;
