import { Icon } from "./Icon.tsx";
import type { ReactNode } from "react";
import "./section-heading.css";

type Tone = "teal" | "raspberry" | "indigo" | "blue" | "slate";

export function SectionIcon({ name, tone }: { name: string; tone: Tone }) {
  return (
    <span className={`section-icon-tile ${tone}`}>
      <Icon name={name} />
    </span>
  );
}

export function SectionHeading({
  title,
  eyebrow,
  icon,
  tone,
}: {
  title: ReactNode;
  eyebrow?: string;
  icon: string;
  tone: Tone;
}) {
  return (
    <div className="section-heading">
      <SectionIcon name={icon} tone={tone} />
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h2>{title}</h2>
      </div>
    </div>
  );
}
