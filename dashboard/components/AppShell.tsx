"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { Navbar } from "@/components/Navbar";
import { Footer } from "@/components/Footer";

/** Root chrome: hides the site footer on /world for a rigid full-viewport simulator. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isWorld = pathname.startsWith("/world");
  const isQuant = pathname.startsWith("/quant");

  useEffect(() => {
    const root = document.documentElement;
    const rigid = isWorld || isQuant;
    if (rigid) {
      root.classList.add("app-viewport");
    } else {
      root.classList.remove("app-viewport");
    }
    if (isWorld) {
      root.classList.add("world-viewport");
    } else {
      root.classList.remove("world-viewport");
    }
    return () => {
      root.classList.remove("app-viewport");
      root.classList.remove("world-viewport");
    };
  }, [isWorld, isQuant]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Navbar />
      <main
        className={
          isWorld
            ? "flex min-h-0 flex-1 flex-col overflow-hidden"
            : isQuant
              ? "flex min-h-0 flex-1 flex-col overflow-hidden"
              : "flex flex-1 flex-col"
        }
      >
        {children}
      </main>
      {!isWorld && !isQuant ? <Footer /> : null}
    </div>
  );
}
