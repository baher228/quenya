export const shops = [
  "Stüssy",
  "Carhartt WIP",
  "Goodhood",
  "Oi Polloi",
  "Aries",
  "Sunspel",
  "Folk",
  "Finisterre",
  "Represent",
];

export const styleOptions = [
  { key: "quiet", label: "Quiet luxury", brief: "quiet luxury" },
  { key: "gorp", label: "Gorpcore", brief: "gorpcore but city-friendly" },
  { key: "techno", label: "Berlin techno", brief: "Berlin techno, all black" },
  { key: "graphic", label: "90s skate", brief: "90s skate, loud graphics" },
] as const;

export const tasteRows = [
  { label: "Technical outerwear", value: 0.8 },
  { label: "Arc'teryx", value: 0.8 },
  { label: "Black", value: 0.7 },
  { label: "Gorpcore", value: 0.7 },
  { label: "Shell jacket", value: 0.6 },
  { label: "Cargo trousers", value: 0.6 },
  { label: "The North Face", value: 0.5 },
  { label: "Fleece", value: -0.4 },
  { label: "Wool", value: -0.5 },
  { label: "Logo-heavy", value: -0.6 },
];

export const faq: { q: string; a: string }[] = [
  {
    q: "What is Quenya?",
    a: "A fashion discovery app for the UK. You describe a style in your own words, swipe through real pieces, and Quenya learns your taste and builds full outfits from it.",
  },
  {
    q: "Can I use it now?",
    a: "Quenya is in invite-only testing. Join the waitlist and we'll email you when there's a place for you.",
  },
  {
    q: "Is there an app?",
    a: "Testers use the web app in the browser today. An iPhone app is in development. Tell us which devices you use in the form so we know where to invite you.",
  },
  {
    q: "Where do the clothes come from?",
    a: "New pieces from around 70 UK shops, including Stüssy, Carhartt WIP (via Goodhood and Oi Polloi), Aries, Sunspel, Folk, Finisterre and Represent, all priced in GBP. They're mixed with live second-hand listings from Vinted.",
  },
  {
    q: "How do I get more second-hand?",
    a: "Turn up the niche slider. The more niche you set a style, the more second-hand Vinted listings it mixes in.",
  },
  {
    q: "Does Quenya sell the clothes?",
    a: "No. Every piece links straight to the shop that sells it, and you buy there.",
  },
  {
    q: "Who can see my styles?",
    a: "Nobody else. Styles and the taste they learn are private to each person.",
  },
];
