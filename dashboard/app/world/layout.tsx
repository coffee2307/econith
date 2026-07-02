import { WorldSimProvider } from "@/contexts/WorldSimContext";

/** World simulator: fill remaining viewport below the navbar (no page scroll). */
export default function WorldLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <WorldSimProvider>
      <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
        {children}
      </div>
    </WorldSimProvider>
  );
}
