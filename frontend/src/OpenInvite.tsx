import { useState } from "react";
import { TESTFLIGHT_URL } from "./constants";

const INVITE_TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,}$/;

export default function OpenInvitePage() {
  const invite = new URLSearchParams(window.location.search).get("invite") ?? "";
  const token = INVITE_TOKEN_PATTERN.test(invite) ? invite : null;
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const openUrl = token
    ? new URL("/open", window.location.origin).toString() +
      `?invite=${encodeURIComponent(token)}`
    : "";
  const appUrl = token
    ? `quenya://invite?token=${encodeURIComponent(token)}`
    : "";

  const copyInvite = async () => {
    try {
      await navigator.clipboard.writeText(openUrl);
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  };

  return (
    <div className="stage invite-landing-stage">
      <section className="phone invite-landing">
        <header className="top">
          <span className="brand">QUENYA</span>
        </header>
        <main className="body invite-landing-body">
          <div className="invite-landing-content">
            <p className="eyebrow">iPhone app</p>
            <h1 className="h1">Open Quenya on your iPhone</h1>
            {token ? (
              <>
                <p className="summary invite-landing-copy">
                  Your personal sign-in link is ready.
                </p>
                <a className="btn primary wide center" href={appUrl}>
                  Open in the app
                </a>
                <a className="invite-testflight" href={TESTFLIGHT_URL}>
                  Don&apos;t have the app yet? Install from TestFlight
                </a>
                <section className="panel invite-fallback">
                  <p className="small muted">
                    If the button does nothing, copy your invite below and paste it into the
                    app&apos;s sign-in screen
                  </p>
                  <button className="btn ghost wide" type="button" onClick={() => void copyInvite()}>
                    {copied ? "Copied" : "Copy invite link"}
                  </button>
                  {copyError && (
                    <p className="small muted invite-copy-error" role="status">
                      Couldn&apos;t copy the link. Try opening this page in Safari.
                    </p>
                  )}
                </section>
              </>
            ) : (
              <p className="invite-incomplete">This link is incomplete.</p>
            )}
          </div>
        </main>
      </section>
    </div>
  );
}
