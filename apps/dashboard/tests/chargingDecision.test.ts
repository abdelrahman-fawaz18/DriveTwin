import assert from "node:assert/strict";
import test from "node:test";
import { remainingArrival } from "../src/features/trip/arrival.ts";
import {
  canUseDeferredOffer,
  reviewLimits,
  type ChargingChoice,
} from "../src/features/charging/chargingDecision.ts";

const terms = {
  minimumSoc: 0.10454,
  waitAndChargeS: 105.4,
  arrivalWorkingS: 872,
  offRoute: false,
  stationState: "available",
};
const reviewed: ChargingChoice = {
  bay: "bay-2",
  digest: "first-offer",
  targetSoc: 0.12,
  terms,
};

test("a deferred approval accepts a fresh on-route offer within the reviewed limits", () => {
  const next = { ...reviewed, digest: "next-offer" };
  assert.deepEqual(reviewLimits(reviewed), {
    minimumPercent: 10.4,
    stopSeconds: 120,
  });
  assert.ok(canUseDeferredOffer(reviewed, next));
  assert.equal(canUseDeferredOffer(reviewed, { ...next, bay: "bay-1" }), false);
  assert.equal(
    canUseDeferredOffer(reviewed, { ...next, targetSoc: 0.13 }),
    false,
  );
  for (const change of [
    { stationState: "occupied" },
    { minimumSoc: 0.1039 },
    { waitAndChargeS: 121 },
    { offRoute: true },
  ]) {
    assert.equal(
      canUseDeferredOffer(reviewed, {
        ...next,
        terms: { ...terms, ...change },
      }),
      false,
    );
  }
  const detour = { ...reviewed, terms: { ...terms, offRoute: true } };
  assert.ok(canUseDeferredOffer(detour, detour));
  assert.equal(
    canUseDeferredOffer(detour, { ...detour, digest: next.digest }),
    false,
  );
  const pinned = {
    ...detour,
    terms: { ...detour.terms, detour: "route-plan" },
  };
  assert.ok(canUseDeferredOffer(pinned, { ...pinned, digest: next.digest }));
  assert.equal(
    canUseDeferredOffer(pinned, {
      ...pinned,
      digest: next.digest,
      terms: { ...pinned.terms, detour: "other-plan" },
    }),
    false,
  );
});

test("arrival estimates count down on working time and hide when exceeded", () => {
  assert.equal(remainingArrival(210, 10), "3 min 20 s working time");
  assert.equal(remainingArrival(210, 11), "3 min 19 s working time");
  assert.equal(remainingArrival(3620, 10), "1 h 10 s working time");
  assert.equal(remainingArrival(210, 210), "Due now");
  assert.equal(remainingArrival(210, 211), null);
  assert.equal(remainingArrival(210, -1), null);
  assert.equal(remainingArrival(undefined, 10), null);
});
