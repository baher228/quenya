import { Silhouette } from "./Silhouette";
import type { ReactNode } from "react";

export const CONTACT_EMAIL = "hello@quenya.co.uk";

export function Header({ home = true }: { home?: boolean }) {
  return (
    <header className="site-header">
      <div className="header-row">
        <a className="wordmark-small" href="/" aria-label="Quenya home">
          <Silhouette className="brand-mark" />
          <span className="brand-name">quenya.</span>
        </a>
        <nav className="top-nav" aria-label="Primary">
          <a href={home ? "#faq" : "/#faq"}>Questions</a>
          <a className="top-cta" href={home ? "#waitlist" : "/#waitlist"}>Join the waitlist</a>
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-links">
        <a href="/privacy">Privacy</a>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        <span>© {new Date().getFullYear()} Quenya</span>
        <span>Made in London</span>
      </div>
      <div className="footer-wordmark" aria-hidden="true">quenya.</div>
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
