import type { Metadata } from 'next';
import '../styles/globals.css';
import { Providers } from '@/components/Providers';

export const metadata: Metadata = {
  title: 'SMMS · Social Media Management',
  description: 'AI-powered, plug-and-play social media manager.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
