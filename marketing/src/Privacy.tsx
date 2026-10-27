import { CONTACT_EMAIL, Page } from "./Shell";

function Confirm({ children }: { children: string }) {
  return <mark className="todo" title="Placeholder: the site owner must confirm this">{children}</mark>;
}

export function Privacy() {
  return (
    <Page home={false}>
      <article className="legal">
        <p className="kicker">Privacy notice · Tester waitlist</p>
        <h1 className="display small">Your waitlist details.</h1>
        <p className="lede">
          This notice explains what we keep when you join the Quenya tester waitlist, why, and how to have it deleted.
          It covers the waitlist only. Last updated October 2026.
        </p>

        <h2>Who we are</h2>
        <p>
          Quenya is the data controller for waitlist signups. You can reach us at{" "}
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> <Confirm>contact email: owner to confirm</Confirm>.
        </p>

        <h2>What we store</h2>
        <ul>
          <li>Your email address (required).</li>
          <li>If you give them: your first name, your Instagram or TikTok handle, and a few words about your style.</li>
          <li>Whether you want to test early, and which devices you'd test on (iPhone, Android, web).</li>
          <li>When you signed up, when you last updated your details, and when you gave consent.</li>
          <li>Where the signup came from (for example, a campaign link) and your browser's user-agent string.</li>
        </ul>
        <p>
          Your IP address is used briefly in memory to stop automated spam signups. It is not saved with your details.
          This site sets no cookies and runs no advertising or tracking scripts.
        </p>

        <h2>Why we store it</h2>
        <p>
          We use these details only to run the waitlist: to invite you to test Quenya, to email you about the waitlist and
          testing, and to understand which devices and styles testers care about. We rely on your consent (UK GDPR
          Article 6(1)(a)), which you give with the tick box on the form.
        </p>
        <p>We don't sell your details or share them with advertisers.</p>

        <h2>Who handles it</h2>
        <p>
          Your details are stored on the servers that run Quenya, operated for us by our hosting and database providers,
          who act only on our instructions. <Confirm>list of processors: owner to confirm</Confirm>
        </p>

        <h2>How long we keep it</h2>
        <p>
          Until you ask us to delete it, withdraw consent, or we close the waitlist, whichever comes first. Then we
          delete it.
        </p>

        <h2>Deleting your details, and your other rights</h2>
        <p>
          To be removed, email <a href={`mailto:${CONTACT_EMAIL}?subject=Delete%20my%20waitlist%20details`}>{CONTACT_EMAIL}</a>{" "}
          from the address you signed up with, with the subject “Delete my waitlist details”. We'll delete your entry and
          confirm within one month.
        </p>
        <p>
          You can also ask for a copy of what we hold, ask us to correct it, or withdraw consent at any time. Withdrawing
          consent doesn't affect anything we did before. If you're unhappy with how we've handled your data, you can
          complain to the Information Commissioner's Office at <a href="https://ico.org.uk">ico.org.uk</a>.
        </p>
      </article>
    </Page>
  );
}
