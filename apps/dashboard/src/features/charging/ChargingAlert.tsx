import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "../../ui/Icon.tsx";
import type { Advisory } from "./useChargingAdvisory.ts";

type AlertKind = "reserve" | "recommendation";
export type ReviewOffer = Pick<
  NonNullable<Advisory["estimate"]>,
  | "bay_id"
  | "target_soc"
  | "minimum_soc"
  | "charging_working_s"
  | "off_route"
  | "detour_sha256"
  | "fallback_only"
  | "station_state"
  | "wait_working_s"
  | "candidate_sha256"
  | "arrival_working_s"
>;
type ReserveWarning = { minimum: number; threshold: number | null };
type Alert = {
  asset: string;
  run: string;
  warning: ReserveWarning | null;
  offer: ReviewOffer | null;
};
type Episode = { reserve: boolean; bays: Set<string> };

export function useChargingAlert({
  signedIn,
  asset,
  run,
  live,
  minimum,
  threshold,
  estimate,
  cleared,
}: {
  signedIn: boolean;
  asset: string;
  run: string;
  live: boolean;
  minimum: number | null;
  threshold: number | null;
  estimate: Advisory["estimate"];
  cleared: boolean;
}) {
  const episodes = useRef(new Map<string, Episode>());
  const [alert, setAlert] = useState<Alert | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!signedIn) {
      episodes.current.clear();
      setAlert(null);
      setOpen(false);
      return;
    }
    if (!run) return;
    const scope = `${run}:${asset}`;
    if (cleared) {
      episodes.current.delete(scope);
      setAlert((previous) =>
        previous?.run === run && previous.asset === asset ? null : previous,
      );
      return;
    }
    if (!live || (minimum === null && !estimate)) return;
    const episode = episodes.current.get(scope) ?? {
      reserve: false,
      bays: new Set<string>(),
    };
    const firstRisk = minimum !== null && !episode.reserve;
    const newBay = estimate && !episode.bays.has(estimate.bay_id);
    if (minimum !== null) episode.reserve = true;
    if (estimate) episode.bays.add(estimate.bay_id);
    episodes.current.set(scope, episode);
    if (firstRisk || newBay) setOpen(true);
    setAlert((previous) => {
      const same =
        previous?.run === run && previous.asset === asset ? previous : null;
      if (!same && !firstRisk && !newBay) return previous;
      const warning =
        minimum === null ? (same?.warning ?? null) : { minimum, threshold };
      const offer = estimate
        ? {
            bay_id: estimate.bay_id,
            target_soc: estimate.target_soc,
            minimum_soc: estimate.minimum_soc,
            charging_working_s: estimate.charging_working_s,
            off_route: estimate.off_route,
            detour_sha256: estimate.detour_sha256,
            fallback_only: estimate.fallback_only,
            station_state: estimate.station_state,
            wait_working_s: estimate.wait_working_s,
            candidate_sha256: estimate.candidate_sha256,
            arrival_working_s: estimate.arrival_working_s,
          }
        : (same?.offer ?? null);
      return { asset, run, warning, offer };
    });
  }, [signedIn, asset, run, live, minimum, threshold, estimate, cleared]);

  const current =
    signedIn && alert?.asset === asset && (!run || alert.run === run)
      ? alert
      : null;

  return {
    current,
    kind:
      current && open
        ? current.offer
          ? ("recommendation" as const)
          : ("reserve" as const)
        : null,
    dismiss: () => setOpen(false),
    review: () => setOpen(true),
  };
}

export function reserveWarningText({ minimum, threshold }: ReserveWarning) {
  const reserve =
    threshold === null
      ? "battery reserve"
      : `${+(threshold * 100).toFixed(2)}% reserve`;
  const lowest = (minimum * 100).toFixed(1);
  return threshold !== null && +lowest >= threshold * 100
    ? `The forecast predicts a dip below the ${reserve}.`
    : `Predicted lowest charge is ${lowest}%, below the ${reserve}.`;
}

export function ChargingAlert({
  kind,
  message,
  status,
  onDismiss,
  children,
}: {
  kind: AlertKind;
  message: string;
  status?: string;
  onDismiss: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const description = useId();
  useEffect(() => {
    const element = dialog.current!;
    const previouslyFocused = document.activeElement;
    element.showModal();
    return () => {
      element.close();
      if (
        previouslyFocused instanceof HTMLElement &&
        previouslyFocused.isConnected
      )
        previouslyFocused.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      role="alertdialog"
      className="charging-alert"
      aria-labelledby={title}
      aria-describedby={description}
      onCancel={(event) => {
        event.preventDefault();
        onDismiss();
      }}
    >
      <header className="charging-alert-header">
        <div>
          <Icon name="Energy" />
          <h2 id={title}>
            {kind === "recommendation"
              ? "Charging stop recommended"
              : "Battery reserve warning"}
          </h2>
        </div>
        <button
          type="button"
          className="secondary"
          onClick={onDismiss}
          autoFocus
        >
          Hide alert
        </button>
      </header>
      <p id={description} className="charging-alert-message">
        {message}
      </p>
      {status && (
        <p role="status" className="charging-alert-status">
          {status}
        </p>
      )}
      {children}
    </dialog>
  );
}
