import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Icon, Logo } from "../ui/Icon.tsx";
import { tabs, titles, descriptions } from "./presentation.ts";
import type { Tab } from "./presentation.ts";
import fontLicenseUrl from "../ui/assets/Inter-LICENSE.txt?url";

export function DashboardShell({
  view,
  navigate,
  disconnect,
  charging = false,
  replay,
  toolbar,
  children,
  sidebarFooter,
  disconnectLabel = "Disconnect",
  description,
}: {
  view: Tab;
  navigate: (tab: Tab) => void;
  disconnect?: () => void;
  charging?: boolean;
  replay?: () => void;
  toolbar?: ReactNode;
  children: ReactNode;
  sidebarFooter?: ReactNode;
  disconnectLabel?: string;
  description?: string;
}) {
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    const dismiss = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      for (const disclosure of document.querySelectorAll<HTMLDetailsElement>(
        "details[data-dismiss-outside][open]",
      )) {
        if (event instanceof KeyboardEvent) {
          if (disclosure.contains(document.activeElement)) {
            disclosure.open = false;
            disclosure.querySelector("summary")?.focus();
          }
        } else if (!event.composedPath().includes(disclosure)) {
          disclosure.open = false;
        }
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", dismiss);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", dismiss);
    };
  }, []);
  const open = (tab: Tab) => {
    navigate(tab);
    setMenu(false);
    window.scrollTo({ top: 0 });
  };
  return (
    <div className="drivetwin-connected">
      <aside className={`sidebar ${menu ? "open" : ""}`}>
        <a
          className="brand"
          href="#overview"
          aria-label="DriveTwin home"
          onClick={(e) => {
            e.preventDefault();
            open("Overview");
          }}
        >
          <Logo />
          <span>DriveTwin</span>
        </a>
        <nav aria-label="Main navigation">
          {tabs.map((name) => (
            <button
              key={name}
              className={view === name ? "active" : ""}
              aria-current={view === name ? "page" : undefined}
              title={
                name === "Operator" && charging
                  ? "Review charging recommendations and decisions"
                  : descriptions[name]
              }
              onClick={() => open(name)}
            >
              <Icon name={name} size={21} />
              <span>
                {name === "Energy"
                  ? "Energy Forecast"
                  : name === "Operator" && charging
                    ? "Charging Advisor"
                    : name}
              </span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {replay && (
            <button
              className="replay-nav"
              onClick={replay}
              title="Open the recording with your dashboard sign-in"
            >
              <Icon name="History" size={18} />
              Open replay
              <Icon name="external" size={15} />
            </button>
          )}
          {sidebarFooter ?? <span className="fine">Vehicle monitoring</span>}
        </div>
      </aside>
      {menu && (
        <button
          className="nav-scrim"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="Open navigation"
            onClick={() => setMenu(!menu)}
          >
            <Icon name="menu" />
          </button>
          {toolbar ?? (
            <span className="location-label">
              DriveTwin <span>·</span> Vehicle monitoring
            </span>
          )}
          <div className="header-status">
            {disconnect && (
              <button className="secondary" onClick={disconnect}>
                {disconnectLabel}
              </button>
            )}
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <h1>
                {view === "Operator" && charging
                  ? "Charging decisions"
                  : titles[view]}
              </h1>
              <p>
                {description ??
                  (view === "Operator" && charging
                    ? "Review charging options and follow the bus through the stop."
                    : descriptions[view])}
              </p>
            </div>
          </div>
          <div className="page-content">{children}</div>
          <footer className="workspace-footer">
            <span>DriveTwin</span>
            <span>
              Vehicle intelligence ·{" "}
              <a href={fontLicenseUrl} target="_blank" rel="noreferrer">
                Font license
              </a>
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
