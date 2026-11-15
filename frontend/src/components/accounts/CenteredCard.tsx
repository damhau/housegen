import type { ReactNode } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

/** The single card of the account pages (sign-in, first-login wizard, invitation link). */
export function CenteredCard({
  title,
  description,
  aside,
  className,
  children,
}: {
  title: ReactNode
  description?: ReactNode
  aside?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-full items-start justify-center px-4 py-10 sm:py-16">
      <Card className={cn("w-full max-w-md", className)}>
        <CardHeader>
          {aside}
          <CardTitle className="text-lg">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
        <CardContent className="grid gap-4">{children}</CardContent>
      </Card>
    </div>
  )
}
