import type { ReactNode } from "react"
import { Link } from "@tanstack/react-router"
import { CONTACT_EMAIL, OPERATOR } from "@/lib/legal"

/** The public pages (about, privacy policy): readable signed out, as Google's OAuth branding requires. */
export function LegalPage({ title, lead, children }: { title: string; lead?: ReactNode; children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto">
      <article className="mx-auto max-w-2xl px-4 py-10 text-sm leading-relaxed sm:py-14">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {lead && <div className="mt-2 text-muted-foreground">{lead}</div>}
        <div className="mt-8 grid gap-8">{children}</div>
        <footer className="mt-12 flex flex-wrap gap-x-4 gap-y-1 border-t pt-4 text-xs text-muted-foreground">
          <span>Run by {OPERATOR}, Switzerland</span>
          <a className="underline-offset-4 hover:underline" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          <Link to="/about" className="underline-offset-4 hover:underline">
            About
          </Link>
          <Link to="/privacy" className="underline-offset-4 hover:underline">
            Privacy policy
          </Link>
        </footer>
      </article>
    </div>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  )
}

export function Bullets({ children }: { children: ReactNode }) {
  return <ul className="grid list-disc gap-1.5 pl-5">{children}</ul>
}
