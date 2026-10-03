"use client";

import { MainControlDashboard } from "@/components/MainControlDashboard";
import { JournalistTicker } from "@/components/JournalistTicker";

export function LandingPage() {
  return (
    <main className="mx-auto flex min-h-[calc(100vh-3.5rem)] w-full max-w-7xl flex-col gap-4 p-4 sm:p-6">
      <MainControlDashboard />
      <JournalistTicker />
    </main>
  );
}
