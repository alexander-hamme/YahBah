import type { Metadata } from "next";
import { DM_Sans, Sora, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Providers } from "@/components/providers";
import "./globals.css";

const dmSans = DM_Sans({
  variable: "--font-dm-sans",
  subsets: ["latin"],
});

const sora = Sora({
  variable: "--font-sora",
  subsets: ["latin"],
  weight: ["600", "700", "800"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "YahBah Dashboard",
  description: "Autonomous job application system dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${dmSans.variable} ${sora.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background">
        <Providers>
          <header className="sticky top-0 z-40 border-b bg-card/80 backdrop-blur-md">
            <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-6">
              <Link
                href="/applications"
                className="flex items-center gap-2 text-lg font-bold tracking-tight text-primary hover:opacity-80 transition-opacity"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/yahbah-logo.svg" alt="YahBah" width={32} height={32} className="rounded" />
                YahBah
              </Link>
              <nav className="flex gap-1">
                <Link
                  href="/applications"
                  className="px-3 py-1.5 text-sm font-medium text-muted-foreground rounded-md hover:text-foreground hover:bg-accent transition-colors"
                >
                  Applications
                </Link>
              </nav>
            </div>
            <div className="h-[2px] bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
          </header>
          <main className="flex-1">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
