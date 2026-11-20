import fs from "node:fs";
import crypto from "node:crypto";
import { time, value } from "../../apps/dashboard/src/monitoring/model.ts";
const inputs = [
  "apps/dashboard/src/monitoring/model.ts",
  "scripts/monitoring/profile_presentation.mjs",
];
const date = "2026-11-04T12:34:56Z";
const priorTime = () =>
  new Date(date).toLocaleTimeString("en-US", {
    hour12: false,
    timeZone: "UTC",
  }) + " UTC";
const priorValue = () =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }).format(
    12345.6789,
  );
if (time(date) !== priorTime() || value(12345.6789) !== priorValue())
  throw Error("Formatting changed");
function measure(fn) {
  const samples = [];
  for (let repetition = 0; repetition < 3; repetition++) {
    const start = performance.now();
    for (let call = 0; call < 200; call++) fn();
    samples.push((performance.now() - start) / 1000);
  }
  return samples;
}
console.log(
  JSON.stringify({
    scope:
      "Offline presentation-helper profiling; not whole-browser RAM or live latency acceptance",
    identical_display_text: true,
    node: process.version,
    icu: process.versions.icu,
    repetitions: 3,
    calls_per_case: 200,
    prior_time_s: measure(priorTime),
    cached_time_s: measure(() => time(date)),
    prior_number_s: measure(priorValue),
    cached_number_s: measure(() => value(12345.6789)),
    source_sha256: Object.fromEntries(
      inputs.map((p) => [
        p,
        crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex"),
      ]),
    ),
  }),
);
