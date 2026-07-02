import type { Metadata } from "next";
import "@fortawesome/fontawesome-svg-core/styles.css";
import "@/lib/fontawesome";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { Providers } from "@/components/Providers";
import { ThemeScript } from "@/components/ThemeScript";
import { LocaleScript } from "@/components/LocaleScript";

export const metadata: Metadata = {
  title: "ECONITH :: Quant Research & World Simulator",
  description:
    "Unified super-platform: AI Quant Research Platform and Financial World Simulator.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <ThemeScript />
        <LocaleScript />
      </head>
      <body className="flex min-h-screen flex-col bg-base font-sans text-ink">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
