import type { ReactNode } from "react";
import { Icon } from "../ui/Icon.tsx";
import { number, sampleValue } from "./data.ts";
import type { Mode, Tab, Vehicle } from "./data.ts";
export interface PreviewContext {
  time: number;
  age: number;
  mode: Mode;
  vehicle: Vehicle;
  historySignal: string;
  setTab: (tab: Tab) => void;
  openHistory: (id: string) => void;
}
export const isStale = (c: PreviewContext) =>
  c.mode !== "Missing data" &&
  (c.age > 5 || c.mode === "Disconnected" || c.mode === "Stale");
export const currentValue = (id: string, c: PreviewContext) =>
  c.mode === "Missing data" ||
  (c.mode === "Invalid reading" && id === "traction.motor.temperature")
    ? null
    : sampleValue(id, c.time, c.vehicle);
export function Panel({
  title,
  children,
  className = "",
  action,
}: {
  title?: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <h2>{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Metric({
  label,
  value,
  unit = "",
  icon,
  tone,
  caption,
}: {
  label: string;
  value: number | null;
  unit?: string;
  icon: string;
  tone: string;
  caption?: string;
}) {
  return (
    <section className={`metric-card gradient ${tone}`}>
      <div className="metric-title">
        <Icon name={icon} size={23} />
        <span>{label}</span>
      </div>
      <div className="metric-number">
        {number(value, 1)}
        <span>{unit}</span>
      </div>
      <small>{caption}</small>
    </section>
  );
}
export function Status({
  label,
  tone = "teal",
}: {
  label: string;
  tone?: string;
}) {
  return (
    <span className={`status ${tone}`}>
      <span />
      {label}
    </span>
  );
}
export function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <Icon name="Measurements" size={32} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
export function Details({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <details className="panel disclosure">
      <summary>
        {title}
        <Icon name="chevron" size={18} />
      </summary>
      <div className="disclosure-content">{children}</div>
    </details>
  );
}
