import type { NextConfig } from 'next';

const isDevelopment = process.env.NODE_ENV === 'development';
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob:${isDevelopment ? " 'unsafe-eval'" : ''}`,
  `connect-src 'self'${isDevelopment ? ' ws:' : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

const securityHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  },
];

const nextConfig: NextConfig = {
  devIndicators: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
      {
        source: '/cad-worker.mjs',
        headers: [{ key: 'Cache-Control', value: 'no-cache' }],
      },
      {
        source: '/cad-runtime/:path*',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=31536000, immutable',
          },
        ],
      },
    ];
  },
  transpilePackages: [
    '@amagine3d/cad-agent',
    '@amagine3d/cad-execution-browser',
    '@amagine3d/cad-protocol',
    '@amagine3d/cad-storage-opfs',
    '@amagine3d/cad-viewer',
    '@amagine3d/web-research',
  ],
};

export default nextConfig;
