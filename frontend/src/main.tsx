import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider, createRouter } from "@tanstack/react-router"
import { routeTree } from "./routeTree.gen"
import { ApiError } from "./api/http-client"
import { onApiError } from "./lib/auth"
import "./index.css"

// a deploy swaps the single pod in 20–40 s: keep retrying network errors and 5xx from the
// ingress for about a minute instead of failing the page after the default few seconds
const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: (e) => onApiError(queryClient, e) }),
  mutationCache: new MutationCache({ onError: (e) => onApiError(queryClient, e) }),
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 20,
      retryDelay: 3000,
    },
  },
})
const router = createRouter({ routeTree, context: { queryClient } })

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
