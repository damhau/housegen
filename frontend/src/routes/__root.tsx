import { createRootRouteWithContext, Link, Outlet } from "@tanstack/react-router"
import type { QueryClient } from "@tanstack/react-query"
import { Home } from "lucide-react"
import { useHealth } from "@/api/endpoints/meta/meta"

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootLayout,
})

function RootLayout() {
  // the running build: "1.2.3" (release) or "sha-abc1234" (main); "dev" outside the image
  const health = useHealth({ query: { staleTime: Infinity, retry: false } })
  const build = health.data
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-4">
        <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
            <Home className="size-3.5" />
          </span>
          housegen
        </Link>
        <span className="text-xs text-muted-foreground">plan + photos → 3D, with an agent that looks at its own work</span>
        {build && (
          <span
            className="ml-auto font-mono text-[11px] tabular-nums text-muted-foreground"
            title={`${build.env}${build.commit ? ` · commit ${build.commit}` : ""}`}
          >
            {build.version}
          </span>
        )}
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
