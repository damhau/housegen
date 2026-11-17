import { createFileRoute } from "@tanstack/react-router"
import { Bullets, LegalPage, Section } from "@/components/legal/LegalPage"
import { CONTACT_EMAIL, OPERATOR, POLICY_UPDATED } from "@/lib/legal"

export const Route = createFileRoute("/privacy")({ component: PrivacyPage })

const mail = (
  <a className="underline underline-offset-4" href={`mailto:${CONTACT_EMAIL}`}>
    {CONTACT_EMAIL}
  </a>
)

function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy" lead={`Last updated ${POLICY_UPDATED}.`}>
      <Section title="Who is responsible">
        <p>
          housegen is run by {OPERATOR}, Switzerland, which is responsible for the personal data described here.
          Questions and requests: {mail}.
        </p>
      </Section>

      <Section title="What housegen collects">
        <Bullets>
          <li>
            <strong>Your account.</strong> When you sign in with Google or Microsoft, housegen receives your name,
            your email address, whether the provider verified that address, and the identifier of your account with
            that provider. It receives nothing else: no contacts, files, mail or calendar.
          </li>
          <li>
            <strong>Your organisation.</strong> Its name, its members and their roles, the invitations sent (the
            invited email address, and when its email went out), and how much of its limits it uses (jobs started,
            listing photo spend).
          </li>
          <li>
            <strong>Your projects.</strong> The plans, photos and notes you upload, your answers and chat messages, and
            what housegen makes from them: 3D scenes, renders, versions, reviews, listing photos and reports.
          </li>
          <li>
            <strong>One cookie.</strong> <code>housegen_session</code> keeps you signed in for up to 14 days. housegen
            has no analytics, advertising or tracking cookies.
          </li>
          <li>
            <strong>Server logs.</strong> Technical events (requests, job progress, errors) used to run and repair the
            service.
          </li>
        </Bullets>
      </Section>

      <Section title="How it is used">
        <p>
          Only to provide housegen: to sign you in, to show your organisation's projects to its members, and to
          generate, render and share the houses you work on. Your data is not sold, not used for advertising, and not
          used to train AI models.
        </p>
        <p>
          housegen's use of information received from Google APIs adheres to the{" "}
          <a
            className="underline underline-offset-4"
            href="https://developers.google.com/terms/api-services-user-data-policy"
          >
            Google API Services User Data Policy
          </a>
          , including the Limited Use requirements. It asks Google only for your basic profile and email address.
        </p>
      </Section>

      <Section title="Who else processes it">
        <Bullets>
          <li>
            <strong>Hetzner</strong> (Germany) hosts the application, its database and your project files.
          </li>
          <li>
            <strong>OpenAI</strong> (United States) runs the AI model that reads your plans, photos and notes and writes
            the 3D scene. Under its API terms, OpenAI does not train its models on this data.
          </li>
          <li>
            <strong>Modal</strong> (United States) provides the GPU servers that render scenes and listing photos from a
            project's 3D scene.
          </li>
          <li>
            <strong>Google</strong> and <strong>Microsoft</strong> sign you in.
          </li>
          <li>
            <strong>Resend</strong> (United States) delivers the invitation emails: it receives the invited address,
            the name and email address of the owner who invites, and the organisation's name. The emails carry no
            tracking pixels or tracked links.
          </li>
          <li>
            <strong>swisstopo</strong>, <strong>geodienste.ch</strong> and Amazon Web Services' public terrain tiles:
            when you place a project on the map, the address or coordinates you search for are sent to them to fetch
            the terrain, aerial images and buildings around the plot.
          </li>
        </Bullets>
      </Section>

      <Section title="Sharing within housegen">
        <p>
          A project is visible to the members of its organisation. A share link shows one version of a house to anyone
          who has the link, until a member of the organisation revokes it.
        </p>
      </Section>

      <Section title="How long it is kept">
        <p>
          Projects are kept until you delete them; deleting a project deletes its files. Your account and
          organisation are kept while you use housegen; write to {mail} to have them deleted. Server logs are kept for
          a limited time.
        </p>
      </Section>

      <Section title="Your rights">
        <p>
          Under the Swiss Federal Act on Data Protection and, if you are in the European Union, the GDPR, you can ask
          to see, correct, export or delete your personal data, and object to its processing: write to {mail}. You can
          also complain to a data protection authority; in Switzerland, the Federal Data Protection and Information
          Commissioner (FDPIC).
        </p>
      </Section>

      <Section title="Changes">
        <p>When this policy changes, the date at the top changes with it.</p>
      </Section>
    </LegalPage>
  )
}
