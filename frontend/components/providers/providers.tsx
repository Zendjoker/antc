"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ConfirmProvider } from "./confirm-provider";
import { FeedbackProvider } from "./feedback-provider";
import { ThemeProvider } from "./theme-provider";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: true } } }));
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <FeedbackProvider>
          <ConfirmProvider>{children}</ConfirmProvider>
        </FeedbackProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
