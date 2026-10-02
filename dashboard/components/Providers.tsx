"use client";

import { ThemeProvider } from "@/contexts/ThemeContext";
import { LocaleProvider } from "@/contexts/LocaleContext";
import { MetricsProvider } from "@/components/MetricsProvider";
import { WorldSimProvider } from "@/contexts/WorldSimContext";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <LocaleProvider>
        <MetricsProvider>
          <WorldSimProvider>
            <div className="flex min-h-0 flex-1 flex-col">{children}</div>
          </WorldSimProvider>
        </MetricsProvider>
      </LocaleProvider>
    </ThemeProvider>
  );
}
