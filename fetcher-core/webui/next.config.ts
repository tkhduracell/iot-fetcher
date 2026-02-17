import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
  async rewrites() {
    return [
      { source: '/influx/:path*', destination: '/api/influx/:path*' },
      { source: '/sonos/:path*', destination: '/api/sonos/:path*' },
      { source: '/roborock/:path*', destination: '/api/roborock/:path*' },
      { source: '/health', destination: '/api/health' },
      { source: '/metrics/:path*', destination: '/api/metrics/:path*' },
      { source: '/upload', destination: '/api/upload' },
      { source: '/upload/:path*', destination: '/api/upload/:path*' },
    ];
  },
};

export default nextConfig;
