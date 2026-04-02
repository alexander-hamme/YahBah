import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Providers } from "@/components/providers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
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
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-gray-50 dark:bg-gray-950">
        <Providers>
          <header className="border-b bg-white dark:bg-gray-900 px-6 py-3 flex items-center gap-6">
            <Link
              href="/applications"
              className="text-lg font-semibold tracking-tight"
            >
              YahBah
            </Link>
            <nav className="flex gap-4 text-sm text-gray-600 dark:text-gray-400">
              <Link
                href="/applications"
                className="hover:text-gray-900 dark:hover:text-gray-100"
              >
                Applications
              </Link>
            </nav>
          </header>
          <main className="flex-1">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
