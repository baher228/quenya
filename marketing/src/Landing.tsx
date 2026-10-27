import { briefs, faq, layers, shops, steps } from "./content";
import { Page } from "./Shell";
import { Shot } from "./Shot";
import { WaitlistForm } from "./WaitlistForm";

function Ticker() {
  const items = [...briefs, ...briefs];
  return (
    <div className="ticker" aria-hidden="true">
      <div className="ticker-track">
        {items.map((b, i) => (
          <span key={i}>“{b}”</span>
        ))}
      </div>
    </div>
  );
}

export function Landing() {
  return (
    <Page home>
      <section className="hero">
        <div className="hero-copy">
          <p className="kicker">Fashion discovery · UK · Invite-only testing</p>
          <h1 className="display">
            Describe a style. <em>Swipe</em> it into shape.
          </h1>
          <p className="lede">
            Write what you're after in your own words. Quenya deals you real pieces from around 70 UK shops and live
            Vinted listings, learns from every like and pass, then puts whole outfits together.
          </p>
          <div className="hero-actions">
            <a className="btn" href="#waitlist">Join the tester waitlist</a>
            <a className="link" href="#how">How it works</a>
          </div>
        </div>
        <div className="hero-shots">
          <Shot name="outfit" alt="Quenya outfits screen: a layered gorpcore outfit with Lock and Swap on each piece" eager className="tilt-l" />
          <Shot name="deck" alt="Quenya swipe screen: a second-hand Vinted puffer jacket card with Pass and Keep" className="tilt-r" />
        </div>
      </section>

      <Ticker />

      <section className="how" id="how" aria-labelledby="how-title">
        <p className="kicker">How it works</p>
        <h2 id="how-title" className="h2">Three steps, in your own words.</h2>
        <ol className="steps">
          {steps.map(s => (
            <li key={s.n}>
              <span className="step-n">{s.n}</span>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="feature dark" aria-labelledby="f-swipe">
        <div className="feature-copy">
          <p className="chapter"><span>I</span>Swipe</p>
          <h2 id="f-swipe" className="h2">A deck that pays attention.</h2>
          <p>
            Product cards, one at a time, Tinder-style. Keep or pass. Every swipe teaches that style's taste profile
            right away, so the deck keeps moving closer to what you described.
          </p>
          <p className="aside">New and second-hand on the same deck. Every price in GBP.</p>
        </div>
        <Shot name="deck" alt="A Quenya swipe card for a black puffer jacket, £50, Vinted second-hand, with tags" />
      </section>

      <section className="feature" aria-labelledby="f-taste">
        <div className="feature-copy">
          <p className="chapter"><span>II</span>Taste</p>
          <h2 id="f-taste" className="h2">Your taste, written down.</h2>
          <p>
            Each style keeps its own private taste profile. Open it and see what Quenya has learned: the brands,
            colours and pieces a style leans into, and the ones it now steers away from.
          </p>
          <p className="aside">Styles are private to you. Nobody else sees them.</p>
        </div>
        <Shot name="taste" alt="Quenya taste screen titled What we've learned, listing brands and pieces with positive and negative weights" />
      </section>

      <section className="feature dark" aria-labelledby="f-outfits">
        <div className="feature-copy">
          <p className="chapter"><span>III</span>Outfits</p>
          <h2 id="f-outfits" className="h2">Whole looks, layer by layer.</h2>
          <p>
            From what you've kept, Quenya builds complete outfits. Lock the pieces you love, swap the ones you don't,
            and follow each piece straight to the shop to buy.
          </p>
          <ul className="layers" aria-label="How an outfit is layered">
            {layers.map((l, i) => (
              <li key={l}><span>{String(i + 1).padStart(2, "0")}</span>{l}</li>
            ))}
          </ul>
        </div>
        <Shot name="outfit" alt="A Quenya outfit called Black Shell Stack: shell jacket, smock, tee, cargo trousers and trainers" />
      </section>

      <section className="feature" aria-labelledby="f-second">
        <div className="feature-copy">
          <p className="chapter"><span>IV</span>Second-hand</p>
          <h2 id="f-second" className="h2">Turn it up for the deep cuts.</h2>
          <p>
            New pieces sit alongside live second-hand listings from Vinted. The niche slider decides the mix: the more
            niche you set a style, the more second-hand it brings in.
          </p>
          <div className="dial" aria-hidden="true">
            <div className="dial-bar"><span /></div>
            <div className="dial-labels"><span>Familiar · more new</span><span>Niche · more second-hand</span></div>
          </div>
        </div>
        <Shot name="brief" alt="Quenya new style screen: a typed brief, dressing, condition, a How niche slider and a budget slider" />
      </section>

      <section className="shops" aria-labelledby="shops-title">
        <p className="kicker">Where the pieces come from</p>
        <h2 id="shops-title" className="h2">About 70 UK shops, and Vinted.</h2>
        <p className="shop-list">
          {shops.map((s, i) => (
            <span key={s}>
              {s}
              {i < shops.length - 1 ? <i aria-hidden="true"> / </i> : null}
            </span>
          ))}
          <span className="more"> and more.</span>
        </p>
        <p className="muted">Carhartt WIP via Goodhood and Oi Polloi. All prices in GBP.</p>
      </section>

      <section className="faq" id="faq" aria-labelledby="faq-title">
        <p className="kicker">Questions</p>
        <h2 id="faq-title" className="h2">Good to know.</h2>
        <div className="faq-list">
          {faq.map(f => (
            <details key={f.q}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="waitlist" id="waitlist" aria-labelledby="wl-title">
        <div className="waitlist-copy">
          <p className="kicker">Tester waitlist</p>
          <h2 id="wl-title" className="display small">Get on the <em>list</em>.</h2>
          <p>
            Quenya is in invite-only testing. Leave your email and we'll let you know when there's a place. An iPhone
            app is in development, so tell us what you'd test on.
          </p>
        </div>
        <WaitlistForm />
      </section>
    </Page>
  );
}
