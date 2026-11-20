# Charging story media provenance

The README presents the reviewed Gare Centrale–Parc La Fontaine case recorded on November 14, 2026. Its source record is `live-journey-operator-connected-full-01`. The [connected verification](../../verification/moving-bus-charging.md#connected-montréal-case) records its predictions, accepted stop, measured transfer, resumption, and arrival. Original recordings and live captures retain their private storage and scope.

## Dashboard captures

These are new 1600 × 900 browser screenshots of the dashboard implementation at source revision `c5902f1`, displaying selected retained states. A private presentation adapter serves saved readings and calculation results to the compiled interface. Its approval response displays the recorded acknowledgment; it has no connection to SUMO. The captures preserve the interface's normal number formatting and charging-bay naming.

| File | Displayed state | SHA-256 |
|---|---|---|
| `01-recommendation.png` | Working second 3; original no-charge forecast and preferred charging candidate | `9e39df8f1f74fa0e073add27ae7c4d90562e0d1ddfeffedee4c2465a9464d68a` |
| `02-approved.png` | Working second 7; recorded stop-insertion acknowledgment | `2706cec9aa973e67982d34f76d0570014b6d6b62f70797478678d5d7b076e271` |
| `03-measured.png` | Working second 270; transfer completed and motion resumed at 263.8, with the saved post-charge forecast | `15c2e895e942b0bee1c3b614b145d34ae9b023738168c473e4e251883b1470a5` |

The browser timeline is reconstructed as those states are presented. Display connection status and polling times describe that local presentation. Transient live offer-expiry countdowns are omitted because a retained display has no live station lease. The images establish what the recorded values look like in the current interface; the linked verification establishes the original executed outcome.

The capture adapter allowed only locally supplied assets and responses, and reported no page errors or outbound requests. Visible text was checked for credentials, run and command identifiers, and machine paths. Each PNG contains only image-header, image-data, and end chunks. The public copies retain the reviewed capture bytes without image editing. The [privacy guard](../../../scripts/privacy_guard.py) permits only these exact paths and fingerprints; other binaries and changed captures still require review.

## Charge graphic

`charging-result.svg` is an original Matplotlib figure made from the retained readings and saved forecast results. The line joins all 696 one-working-second battery readings. The shaded interval is the observed charging period, from working second 163.2 to 263.6. The recorded minimum of 10.720% includes the simulator's 0.2-working-second steps; the minimum of the one-second readings is 10.722% after rounding.

The comparison uses the saved calculation at working second 3. Its no-charge minimum is 9.33%, and its preferred-stop minimum is 10.45%, rounded to two decimal places. Those two points are model predictions. The recorded charged trip supplies the observed minimum and 4.192 kWh transfer. The graphic does not introduce an uncharged reference measurement or a new model evaluation.

## Artwork and notices

The interface, icons, and figure are original DriveTwin work. The dashboard's [Inter font notice](../../../apps/dashboard/src/ui/assets/README.md) and [software and data inventory](../../rights/README.md) remain applicable. No external map tiles, browser chrome, or personal desktop content appear in these captures. The underlying geographic case uses OpenStreetMap roads and attributed elevation as described in the [geographic verification record](../../verification/energy-geographic.md).
