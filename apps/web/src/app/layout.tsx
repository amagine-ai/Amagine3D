import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import '@fontsource-variable/ibm-plex-sans';
import '@fontsource-variable/jetbrains-mono';

import './styles.css';
import { I18nProvider } from '../lib/i18n';

export const metadata: Metadata = {
  title: 'Amagine3D',
  description: 'Browser-first CAD agent for 3D-printable hardware enclosures.',
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <I18nProvider>{children}</I18nProvider>
      </body>
    </html>
  );
}
