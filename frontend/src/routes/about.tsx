import { createFileRoute, Link } from "@tanstack/react-router"
import { buttonVariants } from "@/components/ui/button"
import { Bullets, LegalPage, Section } from "@/components/legal/LegalPage"

export const Route = createFileRoute("/about")({ component: AboutPage })

function AboutPage() {
  return (
    <LegalPage
      title="housegen"
      lead="A house's plans and photos, made into a 3D model you can walk through, present and share."
    >
      <Section title="What it does">
        <Bullets>
          <li>You upload the plans of a house (PDF) and, if you have them, photos of its façades.</li>
          <li>
            An AI agent writes the 3D scene, renders it, compares the renders with your photos or the elevation
            drawings, and corrects what differs.
          </li>
          <li>You change the house by chat, furnish its rooms in a style, and walk through it in the browser.</li>
          <li>
            housegen also makes listing photos of the rooms and an SIA 416 surface report, and shares a version of a
            house through a link you can revoke.
          </li>
        </Bullets>
      </Section>
      <Section title="Who can use it">
        <p>
          Sign in with your Google or Microsoft account; there is no separate password. Projects belong to an
          organisation, and its owner invites coworkers.
        </p>
        <div className="flex flex-wrap gap-3 pt-2">
          <Link to="/signin" className={buttonVariants()}>
            Sign in
          </Link>
          <Link to="/privacy" className={buttonVariants({ variant: "outline" })}>
            Privacy policy
          </Link>
        </div>
      </Section>
    </LegalPage>
  )
}
