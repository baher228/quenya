import type { ReactNode } from "react";

export const CONTACT_EMAIL = "hello@quenya.co.uk";

export function Header({ home = true }: { home?: boolean }) {
  return (
    <header className="top">
      <a className="mark" href="/" aria-label="Quenya home">
        quenya<span>.</span>
      </a>
      <nav className="top-nav" aria-label="Primary">
        {home && <a href="#how">How it works</a>}
        {home && <a href="#faq">FAQ</a>}
        <a className="top-cta" href={home ? "#waitlist" : "/#waitlist"}>
          Join the waitlist
        </a>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="foot">
      <div className="foot-mark">
        quenya<span>.</span>
      </div>
      <p className="foot-line">Fashion discovery for the UK. Invite-only testing.</p>
      <nav className="foot-nav" aria-label="Footer">
        <a href="/privacy">Privacy</a>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
      </nav>
      <p className="foot-small">© {new Date().getFullYear()} Quenya</p>
    </footer>
  );
}

export function Page({ children, home }: { children: ReactNode; home?: boolean }) {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header home={home} />
      <main id="main">{children}</main>
      <Footer />
    </>
  );
}
