import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ApiError } from "./lib/api";
import { router } from "./router";
import "@fontsource-variable/noto-sans";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      placeholderData: (prev: unknown) => prev,
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
    },
  },
});

// Charts draw text on a canvas and cache its width, so mount once the face is in (or give up after 1.5s).
const fontReady = Promise.race([document.fonts.load('12.5px "Noto Sans Variable"'), new Promise((r) => setTimeout(r, 1500))]).catch(() => {});

fontReady.then(() => createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
));
