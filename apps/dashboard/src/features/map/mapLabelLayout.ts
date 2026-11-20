export interface LabelBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}
export interface MapLabelCandidate {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  priority: number;
}

// Label spacing belongs to screen coordinates: a street name keeps its pixel
// width when the geographic distance represented by that width changes.
export function layoutMapLabels<T extends MapLabelCandidate>(
  candidates: T[],
  width: number,
  height: number,
  obstacles: LabelBox[] = [],
): T[] {
  const placed: T[] = [],
    occupied = [...obstacles],
    names = new Set<string>();
  if (width <= 0 || height <= 0) return placed;
  const distance = (c: T) => (c.x - width / 2) ** 2 + (c.y - height / 2) ** 2;
  for (const c of [...candidates].sort(
    (a, b) => a.priority - b.priority || distance(a) - distance(b),
  )) {
    if (
      names.has(c.key) ||
      ![c.x, c.y, c.width, c.height].every(Number.isFinite) ||
      c.width <= 0 ||
      c.height <= 0
    )
      continue;
    const box = {
      left: c.x - c.width / 2,
      top: c.y - c.height / 2,
      right: c.x + c.width / 2,
      bottom: c.y + c.height / 2,
    };
    if (
      box.left < 8 ||
      box.top < 8 ||
      box.right > width - 8 ||
      box.bottom > height - 8
    )
      continue;
    if (
      occupied.some(
        (o) =>
          box.left < o.right + 8 &&
          box.right > o.left - 8 &&
          box.top < o.bottom + 8 &&
          box.bottom > o.top - 8,
      )
    )
      continue;
    placed.push(c);
    occupied.push(box);
    names.add(c.key);
  }
  return placed;
}
