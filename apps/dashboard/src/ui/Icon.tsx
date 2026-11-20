const paths: Record<string, string> = {
  Overview: "M3 13a9 9 0 0 1 18 0v6H3v-6Zm9 0 4-5M6 12h1m10 0h1M8 7l1 1m3-3v1",
  Fleet: "M5 3h14v16H5zM5 9h14M8 13h1m6 0h1M7 19v2m10-2v2M8 6h8",
  Map: "M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  Measurements: "M2 12h4l3-8 5 16 3-8h5",
  History: "M3 5v5h5M3 10a9 9 0 1 1 1 7m8-12v7l4 2",
  Energy: "m14 2-10 12h7l-1 8 10-12h-7z",
  Activity: "m12 3 10 5-10 5L2 8Zm-10 10 10 5 10-5M2 18l10 5 10-5",
  System:
    "M9 2h6l1 4 4 1 2 5-3 3v4l-5 3-4-2-4 1-4-5 2-4-1-4 4-3zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  Operator: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 22v-3a8 8 0 0 1 16 0v3",
  battery: "M3 6h17v12H3zM20 10h2v4h-2M10 8l-3 5h4l-1 3 5-6h-4l1-2",
  "sound-on": "M3 9h4l5-4v14l-5-4H3zM16 9a4 4 0 0 1 0 6m3-9a8 8 0 0 1 0 12",
  "sound-off": "M3 9h4l5-4v14l-5-4H3zm13 1 5 5m0-5-5 5",
  temperature: "M10 14V4a2 2 0 0 1 4 0v10a5 5 0 1 1-4 0Zm2-6v9",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  external: "M14 3h7v7m0-7L10 14M10 4H4v16h16v-6",
  search: "M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0Zm-2 4 7 7",
  chevron: "m7 10 5 5 5-5",
  play: "m8 4 12 8-12 8z",
  pause: "M8 4v16M16 4v16",
  close: "m5 5 14 14M19 5 5 19",
  check: "m5 12 5 5L20 6",
  alert: "m12 2 10 19H2ZM12 8v5m0 3v1",
  connection:
    "M3 9a14 14 0 0 1 18 0M6 13a9 9 0 0 1 12 0m-8 4a3 3 0 0 1 4 0m-2 3h.01",
  filter: "M3 4h18l-7 8v8l-4-2v-6z",
  download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
  layers: "m12 3 10 5-10 5L2 8Zm-10 10 10 5 10-5M2 18l10 5 10-5",
  locate: "M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6M9 12h6m-3-3v6",
  menu: "M3 6h18M3 12h18M3 18h18",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 6v6l4 2",
  storage:
    "M3 6c0-5 18-5 18 0s-18 5-18 0Zm0 0v12c0 5 18 5 18 0V6M3 12c0 5 18 5 18 0",
  send: "m2 10 20-8-8 20-3-9Zm9 3L22 2",
  code: "m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18",
  monitor: "M2 3h20v14H2zM12 17v4m-5 0h10",
  shield: "m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6z",
  reset: "M4 4v6h6M4 10a9 9 0 1 1 0 5",
  plus: "M12 4v16M4 12h16",
  minus: "M4 12h16",
};
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] ?? paths.Measurements} />
    </svg>
  );
}
export function Logo() {
  return (
    <span className="dt-logo" aria-hidden="true">
      <svg viewBox="0 0 40 40">
        <path
          d="M11 7C31 5 30 12 24 22L18 34H8L19 16C22 11 19 9 11 7Z"
          fill="white"
        />
        <path
          d="M23 7C36 10 33 15 30 34H24L28 18C30 12 27 9 23 7Z"
          fill="white"
          opacity=".85"
        />
      </svg>
    </span>
  );
}
