import type { Metadata } from "next";
import { DM_Sans, Sora, Bungee, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Providers } from "@/components/providers";
import { TestingToggle } from "@/components/testing-toggle";
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

const bungee = Bungee({
  variable: "--font-bungee",
  subsets: ["latin"],
  weight: ["400"],
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
      className={`${dmSans.variable} ${sora.variable} ${bungee.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Providers>
          <header className="sticky top-0 z-40 glass-panel-elevated">
            <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-6">
              <Link
                href="/applications"
                className="flex items-center gap-2.5 text-lg font-bold tracking-tight text-foreground hover:text-primary transition-colors"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/yahbah-logo.svg" alt="YahBah" width={32} height={32} className="rounded" />
                YahBah
              </Link>
              <nav className="flex gap-1">
                <Link
                  href="/applications"
                  className="px-3 py-1.5 text-sm font-medium text-muted-foreground rounded-md hover:text-foreground hover:bg-white/5 transition-colors"
                >
                  Applications
                </Link>
              </nav>
              <div className="ml-auto">
                <TestingToggle />
              </div>
            </div>
            <div className="h-px bg-gradient-to-r from-transparent via-primary/50 to-transparent" />
          </header>
          <main className="flex-1">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
