import type { ReactNode } from "react";

export const CONTACT_EMAIL = "hello@quenya.co.uk";

export function Header({ home = true }: { home?: boolean }) {
  return (
    <header className="site-header">
      <div className="header-row">
        <a className="wordmark-small" href="/" aria-label="Quenya home">QUENYA</a>
        <nav className="top-nav" aria-label="Primary">
          <a href={home ? "#method" : "/#method"}>Method</a>
          <a href={home ? "#index" : "/#index"}>Index</a>
          <a href={home ? "#faq" : "/#faq"}>FAQ</a>
          <a className="top-cta" href={home ? "#waitlist" : "/#waitlist"}>Request invite <span aria-hidden="true">→</span></a>
        </nav>
      </div>
      {home && (
        <div className="meta-strip mono-label">
          <span>Fashion discovery</span>
          <span>London, UK</span>
          <span>Invite-only testing</span>
          <span>Season 01</span>
        </div>
      )}
    </header>
  );
}

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-links mono-label">
        <a href="/privacy">Privacy</a>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        <span>© {new Date().getFullYear()} Quenya</span>
        <span>Made in London</span>
      </div>
      <div className="footer-wordmark" aria-hidden="true">QUENYA</div>
    </footer>
  );
}

export function Page({ children, home }: { children: ReactNode; home?: boolean }) {
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <Header home={home} />
      <main id="main">{children}</main>
      <Footer />
    </>
  );
}
