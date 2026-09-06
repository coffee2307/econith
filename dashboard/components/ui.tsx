import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";

export function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
}) {
  return (
    <div className="max-w-2xl">
      {eyebrow ? (
        <p className="mb-2 font-mono text-xs uppercase tracking-[0.2em] text-faint">
          {eyebrow}
        </p>
      ) : null}
      <h2 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
        {title}
      </h2>
      {description ? (
        <p className="mt-3 text-sm leading-relaxed text-muted">{description}</p>
      ) : null}
    </div>
  );
}

export function FeatureCard({
  icon,
  title,
  children,
}: {
  icon: IconDefinition;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="panel p-5">
      <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-elevated">
        <FontAwesomeIcon icon={icon} className="h-4 w-4 text-accent" />
      </div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-muted">{children}</p>
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="panel p-4">
      <p className="font-mono text-xs uppercase tracking-wider text-faint">
        {label}
      </p>
      <p className="mt-1 text-xl font-semibold text-ink">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}
