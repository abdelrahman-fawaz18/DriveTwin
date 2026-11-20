import { useEffect, useState } from "react";

export function ApprovalClock({
  expiresMs,
  remainingWorking,
  queued = false,
  checking = false,
}: {
  expiresMs?: number;
  remainingWorking?: number;
  queued?: boolean;
  checking?: boolean;
}) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (expiresMs === undefined) return;
    setNow(performance.now());
    const timer = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(timer);
  }, [expiresMs]);
  const real =
    expiresMs === undefined
      ? null
      : Math.max(0, Math.ceil((expiresMs - now) / 1000));
  if (!checking && real === null && remainingWorking === undefined) return null;
  return (
    <div
      className="approval-clock"
      title="The station check may renew this timer. Approval can close sooner if the bus passes the turn or the offer changes."
    >
      <dl>
        {checking && real === null && (
          <div>
            <dt>Current offer</dt>
            <dd>Checking availability</dd>
          </div>
        )}
        {remainingWorking !== undefined && (
          <div>
            <dt>Next review checkpoint</dt>
            <dd>{Math.max(0, Math.ceil(remainingWorking))} working s</dd>
          </div>
        )}
        {real !== null && (
          <div>
            <dt>
              {queued ? "Queued approval ends in" : "Current offer expires in"}
            </dt>
            <dd role="timer">{real} real-life s</dd>
          </div>
        )}
      </dl>
    </div>
  );
}
