# Dashboard preview review

Reviewed November 10, 2026. Scope: the separate sample-data entry point at `apps/dashboard/preview.html`.

| Check | Result |
|---|---|
| Navigation and visual review | All nine tabs opened; shared branding, gradients, controls, charts, and tables inspected |
| Standard readings | All 20 catalog signals present; search, metadata, quality filtering, and History navigation exercised |
| State behavior | Paused working time stayed fixed while age increased; stale/disconnected notices appeared; reconnect resumed updates; missing positions were hidden |
| Map | Local road geometry, route framing, layer switches, marker/coordinate agreement, and unavailable position inspected |
| History | Applied range, missing samples, aligned charts, and CSV/JSON downloads exercised; downloaded row counts, columns, range, and gaps checked |
| Energy | Current out-of-domain forecast suppressed; retained forecast remained inspectable with assumptions and station estimates |
| Operator | Sample session, pending replacement/cancellation, reservation, execution, effect, station intent, and disconnected-action restriction exercised |
| Replay | Play/pause, keyboard seek, reset, recorded readings, focus containment, Escape dismissal, and independent main clock exercised |
| Responsive layout | All nine tabs checked at 390-pixel width; no page overflow beyond the viewport; wide tables scroll within their panels |
| Browser diagnostics | No console errors or warnings observed in the reviewed session |
| Automated dashboard checks | 33 tests passed; TypeScript check, preview build, and formatting check passed |
| Reviewed binary safeguard | Five privacy assertion groups passed, including exact font path/bytes and rejection of replacement or unreviewed binary content |

Screenshots, downloaded sample exports, and the browser review record remain in ignored local design storage. The browser was also reviewed at 1440-pixel width. The preview build includes the local street geometry in its JavaScript bundle and reports Vite's advisory for a chunk larger than 500 kB; compressed JavaScript is approximately 220 kB.

This review covers interface behavior with deterministic display fixtures. Vehicle workloads, real service authentication, forecast execution, and actuator effects were not exercised. Existing release evidence retains its recorded source scope. The next checkpoint is design approval followed by integration with the existing dashboard interfaces and affected browser checks.

[Preview guide](../setup/dashboard-preview.md) · [Visual specification](../design/dashboard.md)
