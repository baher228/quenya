import { CONTACT_EMAIL, Page } from "./Shell";

export function Privacy() {
  return (
    <Page home={false}>
      <article className="legal">
        <p className="kicker">Privacy notice</p>
        <h1 className="display small">Your data with Quenya.</h1>
        <p className="lede">
          This notice covers the tester waitlist and Quenya accounts (web and iPhone app). Last updated November 2026.
        </p>

        <h2>Who we are</h2>
        <p>
          Quenya is the data controller. You can reach us at{" "}
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
        </p>

        <h2>What we store</h2>
        <h3>Waitlist</h3>
        <ul>
          <li>Your email address (required).</li>
          <li>If you give them: your first name, your Instagram or TikTok handle, and a few words about your style.</li>
          <li>Whether you want to test early, and which devices you'd test on (iPhone, Android, web).</li>
          <li>When you signed up, when you last updated your details, and when you gave consent.</li>
          <li>Where the signup came from (for example, a campaign link) and your browser's user-agent string.</li>
        </ul>
        <h3>Quenya accounts</h3>
        <ul>
          <li>Your invite name, role (tester or admin), and sign-in sessions.</li>
          <li>Style briefs, swipe history, taste profiles, outfits, uploads, and routing corrections.</li>
          <li>Technical logs needed to run the service (for example request timing and AI task stats).</li>
        </ul>
        <p>
          Waitlist and admin-login rate limits store short-lived IP or name buckets so we can stop automated spam; they
          are not saved with your waitlist profile. The marketing site sets no cookies and runs no advertising or
          tracking scripts.
        </p>

        <h2>Why we store it</h2>
        <p>
          We use waitlist details only to invite you to test Quenya and to understand which devices and styles testers
          care about, based on your consent (UK GDPR Article 6(1)(a)).
        </p>
        <p>
          We use account data to provide Quenya: ranking listings, learning taste, building outfits, and securing
          access. That processing is necessary to perform the service you asked for (UK GDPR Article 6(1)(b)).
        </p>
        <p>We don't sell your details or share them with advertisers.</p>

        <h2>Who handles it</h2>
        <p>Your details are processed on our behalf by:</p>
        <ul>
          <li>Vercel — hosting for the API and web apps</li>
          <li>Supabase — database and image storage</li>
          <li>Cursor — AI model tasks that power briefs, taste, outfits and tagging</li>
          <li>Tavily — live retail page extraction and search</li>
          <li>Our email provider (SMTP) — waitlist and invite emails when configured</li>
        </ul>
        <p>They act only on our instructions for providing Quenya.</p>

        <h2>How long we keep it</h2>
        <p>
          Waitlist entries: until you ask us to delete them, withdraw consent, or we close the waitlist, whichever
          comes first.
        </p>
        <p>
          Accounts: until you delete your account in the app, or an admin revokes access. Deleted accounts have their
          boards, swipes, outfits, taste data and sessions removed, and the login is revoked.
        </p>

        <h2>Deleting your details, and your other rights</h2>
        <p>
          In the Quenya app or web fitting room, open Taste and choose “Delete account” to erase your account data and
          sign out. You can also reset boards without deleting the account.
        </p>
        <p>
          To be removed from the waitlist, email{" "}
          <a href={`mailto:${CONTACT_EMAIL}?subject=Delete%20my%20waitlist%20details`}>{CONTACT_EMAIL}</a> from the
          address you signed up with, with the subject “Delete my waitlist details”. We'll delete your entry and confirm
          within one month.
        </p>
        <p>
          You can also ask for a copy of what we hold, ask us to correct it, or withdraw waitlist consent at any time.
          Withdrawing consent doesn't affect anything we did before. If you're unhappy with how we've handled your data,
          you can complain to the Information Commissioner's Office at{" "}
          <a href="https://ico.org.uk">ico.org.uk</a>.
        </p>
      </article>
    </Page>
  );
}
