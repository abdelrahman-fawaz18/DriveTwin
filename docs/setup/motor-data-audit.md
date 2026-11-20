# Motor data audit and thermal qualification design

**Result:** The original Paderborn motor data passed the structural audit. It is suitable for planning a **testbench temperature prediction** study. It does not validate the temperature of our simulated bus or identify a real cooling fault.

A thermal model describes how a motor heats and cools. For example, a motor under sustained load may keep getting hotter even if its speed stays constant. To test an estimator, we need a sequence of operating measurements and separate temperature readings to compare against its predictions. A testbench is a laboratory setup that applies controlled speed and load to a motor while recording those signals.

## Source and measured inventory

The source is *Electric Motor Temperature* by Wilhelm Kirchgässner, Oliver Wallscheid, and Joachim Böcker, from the Power Electronics and Electrical Drives department at Paderborn University. The authors studied a torque-controlled automotive permanent-magnet synchronous motor, an electric motor with permanent magnets in its rotating part. The cited setup has a 22 kW nominal and 52 kW maximum rating, with laboratory water-glycol cooling. See the [publisher's data card](https://www.kaggle.com/datasets/wkirgsn/electric-motor-temperature), the authors' [testbench description](https://arxiv.org/html/2103.16323#S3), and the [companion paper record](https://ris.uni-paderborn.de/record/21250). The listing declares [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (Creative Commons Attribution-ShareAlike). Keep the attribution. This checkpoint does not publish original data or review any future derived data package for release.

| Check | Measured result or boundary |
|---|---|
| Original artifact | Publisher ZIP, 122,212,134 bytes; one selected member, `measures_v2.csv` (300,061,411 bytes). The [audit record](../verification/motor-data-audit.json) pins both byte fingerprints. |
| Shape | 1,330,816 rows, 12 numeric signals, and 69 distinct `profile_id` values. Each profile is stored as a contiguous block. A profile is one recording session. |
| Cadence | The publisher specifies 2 hertz, or one sample every 0.5 seconds. At that cadence the rows represent about 184.84 recorded hours. No timestamp column independently establishes spacing, clock drift, or gaps. Do not join profiles on an invented continuous clock. |
| Profile size | 2,176 to 43,971 rows, roughly 18 minutes to 6.1 hours at the declared cadence. |
| Data integrity | Zero missing, nonnumeric, or nonfinite signal values. Zero byte-equivalent full ordered session sequences when the session IDs are excluded. That check does not rule out repeated driving patterns or partial overlaps. |
| Held-out boundary | The audit checks shape, completeness, and fingerprints. No temperature ranges, model fitting, target-dependent feature selection, or test scoring were performed. |

## Signal meanings and transfer limits

**Stator** means the fixed part of the motor; its winding is the wire coil. A **rotor** is the rotating part. An internal testbench reading can be a reference target without being a sensor available to the twin. Units and sensor names below follow the [author's dataset description, Table 5.2 and Figure 5.2](https://digital.ub.uni-paderborn.de/hs/content/titleinfo/7664038) and the [authors' testbench paper](https://arxiv.org/html/2103.16323#S3).

| Testbench columns | Meaning and units | Current DriveTwin counterpart or limit |
|---|---|---|
| `motor_speed`, `torque` | Shaft speed in revolutions per minute (rpm) and torque, the turning effect of a force, in newton-meters (N·m). Convert rpm to radians per second with `rpm × 2π / 60`. | `traction.motor.speed` and `traction.motor.torque` are equivalent values calculated from the synthetic bus's motion and power. Low-speed torque uses a proxy rule. Comparable units do not make these measurements interchangeable. |
| `i_d`, `i_q`, `u_d`, `u_q` | Motor current in amperes and voltage in volts, expressed on two axes rotating with the motor field. | The bus catalog has battery pack electrical values. Pack current is not motor-axis current. Any loss calculation based on these channels needs the right scaling and sign conventions before use. |
| `ambient`, `coolant` | Ambient air and liquid coolant temperatures in degrees Celsius. To compare with the bus catalog's kelvin scale, use `K = °C + 273.15`. | The original bus has assumed ambient temperature and no coolant channel. The second bus has a simulated coolant inlet every two seconds; it is an imposed boundary, not a verified loop, flow meter, or pump response. |
| `stator_winding`, `stator_tooth`, `stator_yoke`, `pm` | Temperature readings at the coil, tooth, and yoke in the stationary part and at the rotating permanent magnet, in degrees Celsius. The magnet measurement is a proxy for rotor temperature. | The bus has one assumed aggregate `traction.motor.temperature` reading at one sample per simulated second. It cannot serve as four independent internal readings or as measured real-motor truth. |
| `profile_id` | Session identifier without physical units. | Use it to keep sessions separate, not as a predictor or proof of collection order. |

References for the bus side: [measurement plant](measurement-plant.md), [catalog](../../catalog/monitoring-signals.json), and [coolant extension](sensor-and-asset-extension.md). The raw testbench file has no cooling-fault labels, coolant flow, pump command, or bus identification. Testbench prediction performance will not establish fault diagnosis, cooling-command effectiveness, or transfer to bus ratings and drive cycles.

## What to freeze next

Maintain two evidence paths. Use the original testbench sessions for an independent prediction study. Use separately generated bus scenarios with known faults for detection and intervention. Do not join the two sources by time, treat missing testbench channels as zero, or copy fitted bench coefficients into the bus as calibrated values.

The later P5-A2 checkpoint froze whole-session assignments and the initial-state and feature availability rules. The reserved temperature outcomes are held for the final model evaluation. [Profile split and signal mapping](motor-profile-split.md)

## Reproduce the audit

From Windows PowerShell in `<checkout>`, with the original ZIP under the ignored `data/downloads/p5-motor/` directory, run:

```powershell
python scripts/thermal/audit_motor_data.py --archive data/downloads/p5-motor/paderborn.zip --member measures_v2.csv --output evidence/p5/my-motor-audit.json
```

The command streams the selected comma-separated table inside the archive and saves a fresh report under ignored `evidence/p5/`. Expect `1,330,816 samples in 69 profiles` and a report path. Confirm the original fingerprints match the [audit record](../verification/motor-data-audit.json) and that all invalid counts are zero. If the file, schema, or fingerprints differ, retain the result separately and review the source version before reusing it. The command does not extract a new raw CSV, start services, or train a model.
