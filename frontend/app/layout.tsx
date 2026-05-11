import type { Metadata } from 'next';
import { Suspense } from 'react';
import '../styles/globals.css';
import { Providers } from '@/components/Providers';
import { TopProgressBar } from '@/components/ui/TopProgressBar';

export const metadata: Metadata = {
  title: 'SMMS · Social Media Management',
  description: 'AI-powered, plug-and-play social media manager.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased">
        {/* Route-change progress bar. Uses useSearchParams which Next
            requires inside a Suspense boundary in the app router. */}
        <Suspense fallback={null}>
          <TopProgressBar />
        </Suspense>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
