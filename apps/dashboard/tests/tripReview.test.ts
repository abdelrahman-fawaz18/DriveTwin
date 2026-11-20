import assert from "node:assert/strict";
import test from "node:test";
import {
  addTripEvent,
  chargingBayName,
  readTripEvents,
  tripEventLabel,
  type TripEvent,
} from "../src/features/trip/tripReview.ts";

const event: TripEvent = {
  id: "reviewed-offer",
  kind: "declined",
  at: "2026-11-14T00:00:00Z",
  working: 0,
  bay: "dev-bay-3",
};

test("charging decisions can be restored with their original clock and bay", () => {
  const events = addTripEvent([], event);
  assert.deepEqual(readTripEvents(JSON.stringify(events)), [event]);
  assert.equal(addTripEvent(events, event), events);
  assert.equal(tripEventLabel(event), "Chose to keep driving · Charging bay 3");
  assert.equal(
    chargingBayName(event.bay!, "West Terminal"),
    "West Terminal · Charging bay 3",
  );
});

test("local activity rejects malformed events and limits retained entries", () => {
  const rows = Array.from({ length: 220 }, (_, i) => ({
    ...event,
    id: String(i),
  }));
  assert.deepEqual(
    readTripEvents(
      JSON.stringify([
        ...rows,
        null,
        { ...event, at: "unknown" },
        { ...event, working: -1 },
      ]),
    ),
    rows.slice(-200),
  );
  assert.deepEqual(readTripEvents("{unfinished"), []);
  assert.deepEqual(readTripEvents("x".repeat(90_001)), []);
});
