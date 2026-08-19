import type { Metadata } from 'next';

import report from '../../../../../third_party/npm-production-licenses.json';
import { LicensesClient } from './licenses-client';

export const metadata: Metadata = {
  title: 'Licenses and notices · Amagine3D',
  description: 'Amagine3D project license and third-party notices.',
};

export default function LicensesPage() {
  return <LicensesClient packages={report.packages} />;
}
