type Props = {
  name: string;
  alt: string;
  eager?: boolean;
  className?: string;
};

// Real screenshots of the live web app (390x844 CSS px, exported at 2x).
export function Shot({ name, alt, eager, className }: Props) {
  return (
    <figure className={`phone ${className ?? ""}`}>
      <img
        src={`/shots/${name}.webp`}
        alt={alt}
        width={780}
        height={1688}
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        {...(eager ? ({ fetchpriority: "high" } as Record<string, string>) : {})}
      />
    </figure>
  );
}
