export type Position = [number, number];

// Display fixture shared by location readings, live map, and replay.
export const route: Position[] = [
  [45.5069, -73.5637],
  [45.5081, -73.5648],
  [45.5104, -73.5668],
  [45.5125, -73.5686],
  [45.5142, -73.5702],
  [45.5158, -73.5717],
  [45.5178, -73.5734],
  [45.5191, -73.5746],
];

export function routePosition(t: number): Position {
  const index =
    (Math.min(599.999, Math.max(0, t % 600)) / 600) * (route.length - 1);
  const a = route[Math.floor(index)]!;
  const b = route[Math.min(route.length - 1, Math.floor(index) + 1)]!;
  const fraction = index % 1;
  return [a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction];
}
