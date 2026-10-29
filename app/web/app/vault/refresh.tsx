"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// While a file is still being checked, refresh the list every few seconds.
export function RefreshWhileChecking({ active }: { active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), 3000);
    return () => clearInterval(timer);
  }, [active, router]);
  return null;
}
