import { useEffect, useEffectEvent, useId, useRef, useState } from "react";
import { Loading } from "../../ui/Loading.tsx";
import { ApprovalClock } from "./approvalClock.tsx";
import {
  canUseDeferredOffer,
  reviewLimits,
  type ChargingChoice,
} from "./chargingDecision.ts";

type RecoveryStatus = {
  held: { bay: string; working_s: number; soc: number } | null;
  phase: "waiting" | "choices" | "no_plan" | null;
  choices: (Choice & {
    fence: number;
    target_soc: number;
    minimum_soc: number;
  })[];
};
type Status = {
  command_id: string | null;
  state: string;
  override_latched?: boolean;
} & Partial<RecoveryStatus>;
type Choice = ChargingChoice;
const states: Record<string, string> = {
  idle: "Ready for an operator decision",
  approved: "Approved · Awaiting handoff",
  expired: "Approval expired",
  vetoed: "Approval withdrawn",
  granted: "Handoff issued · Awaiting simulator acknowledgment",
  applied: "Stop insertion acknowledged",
  refused: "Simulator refused the stop",
  unknown: "Handoff outcome unknown",
  unavailable: "No active charger interruption",
};

export function ChargingApproval({
  run,
  asset,
  token,
  fence,
  offerExpiryMs,
  preferredWorkingExpiry,
  fallbackWorkingExpiry,
  preferredBay,
  workingTime = 0,
  choices,
  requireChoice = false,
  recovery = false,
  automatic = false,
  waitingForRoad = false,
  waitingForMotion = false,
  passedApprovalPoint = false,
  hasRecommendation = false,
  reviewOffer,
  reviewPaused = false,
  reviewClosed = false,
  onDecline,
  onApproved,
  bayName = (bay) => bay,
  declinedBay,
  onReviewAgain,
}: {
  run: string;
  asset: string;
  token: string;
  fence?: number;
  offerExpiryMs?: number;
  preferredWorkingExpiry?: number;
  fallbackWorkingExpiry?: number;
  preferredBay?: string;
  workingTime?: number;
  choices: Choice[];
  requireChoice?: boolean;
  recovery?: boolean;
  automatic?: boolean;
  waitingForRoad?: boolean;
  waitingForMotion?: boolean;
  passedApprovalPoint?: boolean;
  hasRecommendation?: boolean;
  reviewOffer?: Choice;
  reviewPaused?: boolean;
  reviewClosed?: boolean;
  onDecline?: (bay: string, digest: string) => void;
  onApproved?: (command: string, choice: Choice) => void;
  bayName?: (bay: string) => string;
  declinedBay?: string;
  onReviewAgain?: (bay: string) => void;
}) {
  const key = token;
  const [status, setStatus] = useState<Status | null>(null);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [deferred, setDeferred] = useState<{
    choice: Choice;
    until: number;
  } | null>(null);
  const sending = useRef(false);
  const stopSelectId = useId();
  const prefix = `/v2/operator/${encodeURIComponent(run)}/${encodeURIComponent(asset)}/charging${recovery ? "/recovery" : ""}`;

  useEffect(() => {
    if (!key || busy) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    async function poll() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3500);
      try {
        const response = await fetch(prefix + "/status", {
          headers: { Authorization: `Bearer ${key}` },
          signal: controller.signal,
          cache: "no-store",
        });
        if (!active) return;
        if (response.status === 401 || response.status === 403) {
          setStatusError(
            "Your dashboard sign-in no longer has charging approval access.",
          );
          setStatus(null);
          active = false;
          return;
        }
        if (!response.ok) throw Error("status");
        const next: Status = await response.json();
        if (active) {
          setStatus(next);
          setStatusError("");
        }
      } catch {
        if (active) {
          setStatus(null);
          setStatusError(
            "The command service could not confirm the current status.",
          );
        }
      } finally {
        clearTimeout(timeout);
        if (active) timer = setTimeout(poll, 750);
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [busy, key, prefix]);

  const options = recovery ? (status?.choices ?? []) : choices;
  const chosen = candidate
    ? options.find((item) => item.bay === candidate)
    : requireChoice || recovery
      ? undefined
      : options[0];
  const needsSelection = Boolean(
    (requireChoice || recovery || candidate) && !chosen,
  );
  const selectedFence = recovery
    ? status?.choices?.find((item) => item.digest === chosen?.digest)?.fence
    : fence;
  const canApprove =
    !automatic &&
    status &&
    ["idle", "expired", "vetoed"].includes(status.state);
  const showOptions =
    options.length > 1 || requireChoice || recovery || needsSelection;
  const temporarilyBlocked = waitingForRoad || waitingForMotion;
  const workingExpiry =
    chosen?.bay === preferredBay
      ? preferredWorkingExpiry
      : fallbackWorkingExpiry;
  const windowOpen =
    (workingExpiry === undefined || workingTime < workingExpiry) &&
    (offerExpiryMs === undefined || performance.now() < offerExpiryMs);
  const actionable = Boolean(
    key &&
      canApprove &&
      chosen &&
      selectedFence !== undefined &&
      windowOpen &&
      !temporarilyBlocked &&
      !passedApprovalPoint,
  );
  const stableChoice =
    chosen ??
    (!candidate || candidate === reviewOffer?.bay ? reviewOffer : undefined);
  const waitChoice =
    key &&
    canApprove &&
    !reviewClosed &&
    !passedApprovalPoint &&
    !recovery &&
    !automatic &&
    !actionable &&
    stableChoice?.terms?.stationState === "available" &&
    (workingExpiry === undefined || workingTime < workingExpiry)
      ? stableChoice
      : undefined;
  const limits = waitChoice && reviewLimits(waitChoice);
  function approveOrWait() {
    if (actionable) {
      void send("approve");
    } else if (waitChoice && limits) {
      setError("");
      setCandidate(waitChoice.bay);
      setDeferred({ choice: waitChoice, until: performance.now() + 60_000 });
    }
  }
  async function send(action: "approve" | "veto" | "override") {
    if (sending.current || !key) return;
    const digest = chosen?.digest;
    const commandId =
      action === "approve" ? crypto.randomUUID() : status?.command_id;
    if (
      (!commandId && action !== "override") ||
      (action === "approve" &&
        (!digest ||
          selectedFence === undefined ||
          !actionable ||
          (offerExpiryMs !== undefined && performance.now() >= offerExpiryMs)))
    )
      return;
    setError("");
    sending.current = true;
    setBusy(true);
    try {
      const route =
        action === "override" ? "/automatic/override" : "/" + action;
      const response = await fetch(prefix + route, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${key}`,
          "Content-Type": "application/json",
        },
        signal: AbortSignal.timeout(4000),
        body: JSON.stringify({
          run_id: run,
          asset_id: asset,
          ...(action === "override" ? {} : { command_id: commandId }),
          ...(action === "approve"
            ? { job_fence: selectedFence, candidate_sha256: digest }
            : {}),
        }),
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => null);
        setError(
          failure?.detail === "moving_charge_execution_outside_domain"
            ? "This estimate requires station access, power, or dwell outside the operator profile."
            : failure?.detail === "moving_charge_commit_point_passed"
              ? "The bus is crossing a junction or has reached the last point for approving this bay. Review the current offer."
              : response.status === 409
                ? "The offer changed or the handoff has already closed. Review the current status."
                : "The command service refused the request.",
        );
        return;
      }
      const result = await response.json();
      if (action === "override" && status) {
        setStatus({ ...status, override_latched: true });
        if (result.state === "grant_already_issued") {
          setError(
            "The handoff was already issued. The override blocks further automatic approvals.",
          );
        }
      } else {
        setStatus(result);
        if (
          action === "approve" &&
          chosen &&
          commandId &&
          result.state === "approved"
        )
          onApproved?.(commandId, chosen);
      }
    } catch {
      setStatus(null);
      setError(
        "The response was interrupted. Wait for the saved status before acting again.",
      );
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const submitDeferred = useEffectEvent(() => void send("approve"));
  useEffect(() => {
    if (!deferred) return;
    const expire = () => {
      setDeferred(null);
      setError(
        "The waiting period ended. Review the current offer to approve a stop.",
      );
    };
    const remaining = deferred.until - performance.now();
    if (remaining <= 0) {
      expire();
      return;
    }
    if (status && !canApprove) {
      setDeferred(null);
      return;
    }
    if (
      (!key && !reviewPaused) ||
      reviewClosed ||
      passedApprovalPoint ||
      (chosen && !canUseDeferredOffer(deferred.choice, chosen))
    ) {
      setDeferred(null);
      setError(
        "The offer is no longer confirmed on the terms you reviewed. Review the current stop before approving.",
      );
      return;
    }
    if (actionable && !busy && canUseDeferredOffer(deferred.choice, chosen)) {
      setDeferred(null);
      submitDeferred();
      return;
    }
    const timer = setTimeout(expire, remaining);
    return () => clearTimeout(timer);
  }, [
    actionable,
    busy,
    canApprove,
    chosen,
    deferred,
    key,
    passedApprovalPoint,
    reviewPaused,
    reviewClosed,
    status,
  ]);

  return (
    <section aria-label="Charging approval">
      <h3>
        {automatic
          ? "Automatic charging trial"
          : recovery
            ? "Charger outage recovery"
            : "Your decision"}
      </h3>
      {key ? (
        <>
          <p role="status">
            {statusError ||
              (deferred
                ? "Approval queued"
                : status
                  ? automatic && status.override_latched
                    ? "Automatic dispatch blocked for this trip"
                    : automatic && status.state === "idle"
                      ? "Waiting for an eligible automatic offer"
                      : (states[status.state] ?? "Unknown status")
                  : "Checking command status…")}
          </p>
          {!deferred && hasRecommendation && canApprove && !actionable && (
            <p className="charge-action-hint" role="status">
              {passedApprovalPoint
                ? "Approval point passed"
                : waitingForRoad
                  ? "Bus crossing a junction"
                  : waitingForMotion
                    ? "Waiting for the bus to move"
                    : "Refreshing station availability"}
            </p>
          )}
          {recovery && status?.held && (
            <p>
              Bus held at {status.held.bay} at working second{" "}
              {status.held.working_s}. Battery charge{" "}
              {(100 * status.held.soc).toFixed(2)}%.
            </p>
          )}
          {recovery && status?.phase === "waiting" && (
            <Loading label="Checking downstream chargers…" />
          )}
          {recovery && status?.phase === "no_plan" && (
            <p>
              No replacement meets the reserve and station checks. The bus
              remains stopped.
            </p>
          )}
          {canApprove && stableChoice && declinedBay === stableChoice.bay ? (
            <div className="charge-decision-actions">
              <p role="status">Continuing on the planned route.</p>
              <button
                className="secondary"
                type="button"
                onClick={() => onReviewAgain?.(stableChoice.bay)}
              >
                Review stop again
              </button>
            </div>
          ) : (
            ((canApprove && options.length > 0) ||
              (!recovery &&
                !automatic &&
                hasRecommendation &&
                (!status || canApprove))) && (
              <div className="charge-decision-actions">
                {showOptions && options.length > 0 && (
                  <label htmlFor={stopSelectId}>
                    {recovery ? "Replacement charger" : "Charging stop"}
                    <select
                      id={stopSelectId}
                      aria-label={
                        recovery ? "Replacement charger" : "Charging stop"
                      }
                      value={chosen?.bay ?? ""}
                      onChange={(event) => setCandidate(event.target.value)}
                    >
                      {needsSelection && (
                        <option value="" disabled>
                          Select a current offer
                        </option>
                      )}
                      {options.map((item) => (
                        <option key={item.bay} value={item.bay}>
                          {bayName(item.bay)}
                          {item.targetSoc !== undefined &&
                            ` · charge to ${(100 * item.targetSoc).toFixed(1)}%`}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {recovery &&
                  status?.choices
                    ?.filter((item) => item.digest === chosen?.digest)
                    .map((item) => (
                      <p key={item.digest}>
                        Departure target {(100 * item.target_soc).toFixed(2)}% ·
                        Lowest predicted charge{" "}
                        {(100 * item.minimum_soc).toFixed(2)}%
                      </p>
                    ))}
                {!deferred && (chosen || stableChoice) && (
                  <ApprovalClock
                    expiresMs={offerExpiryMs}
                    checking={!chosen || !windowOpen}
                    remainingWorking={
                      workingExpiry === undefined
                        ? undefined
                        : workingExpiry - workingTime
                    }
                  />
                )}
                <div className="charge-primary-actions">
                  <button
                    type="button"
                    className="primary"
                    disabled={
                      busy || (!actionable && !limits) || Boolean(deferred)
                    }
                    onClick={approveOrWait}
                    title={
                      limits && waitChoice
                        ? `Wait up to 60 real-life seconds for this bay and target. Continue only if the predicted minimum stays at least ${limits.minimumPercent.toFixed(1)}% and the wait plus charge stays within ${limits.stopSeconds} working seconds.`
                        : "Approve the current charging stop"
                    }
                  >
                    {recovery
                      ? "Approve replacement charger"
                      : "Approve charging stop"}
                  </button>
                  {!recovery && onDecline && stableChoice && (
                    <button
                      className="secondary"
                      type="button"
                      disabled={busy || Boolean(deferred)}
                      onClick={() =>
                        onDecline(stableChoice.bay, stableChoice.digest)
                      }
                      title="Continue along the current route. You can review this stop again."
                    >
                      Keep driving
                    </button>
                  )}
                </div>
                {limits && waitChoice && !deferred && (
                  <details className="charge-decision-note">
                    <summary>Approval queues during this check</summary>
                    <p>
                      Wait up to 60 real-life seconds for the same bay and
                      target. Minimum predicted charge{" "}
                      {limits.minimumPercent.toFixed(1)}% · up to{" "}
                      {limits.stopSeconds} working seconds for the stop.
                    </p>
                  </details>
                )}
              </div>
            )
          )}
          {automatic && status && !status.override_latched && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void send("override")}
            >
              Block automatic dispatch
            </button>
          )}
          {!automatic && status?.state === "approved" && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void send("veto")}
            >
              Withdraw approval
            </button>
          )}
        </>
      ) : (
        <>
          <p role="status">
            {reviewPaused
              ? "Resume the trip to recheck the charging stop."
              : "Approval requires a live connection."}
          </p>
          {hasRecommendation && !automatic && !recovery && (
            <button type="button" className="primary" disabled>
              Approve charging stop
            </button>
          )}
        </>
      )}
      {deferred && (
        <div className="charge-pending">
          <p role="status">
            Approval pending for {bayName(deferred.choice.bay)}. Waiting for a
            fresh offer and a safe point to apply it.
          </p>
          <ApprovalClock expiresMs={deferred.until} queued />
          <button
            type="button"
            className="secondary"
            onClick={() => setDeferred(null)}
          >
            Cancel pending approval
          </button>
        </div>
      )}
      {busy && <Loading label="Checking command…" />}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
