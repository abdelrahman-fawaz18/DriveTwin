# Motor winding prediction qualification

The model accounts for three things. Current and shaft motion supply heat, the winding stores it, and air or coolant carries it away. A watt measures heat added or removed per second. Heat capacity tells us how many joules change the temperature by one kelvin. Temperature differences of one kelvin and one degree Celsius are the same size.

```text
heat capacity × rate of temperature change
  = generated heat + heat exchanged with air, coolant, and adjacent parts
```

The exact update assumes each input is constant during one sample interval. In an insulated case, 30 watts for 60 seconds adds 1,800 joules. A heat capacity of 600 joules per kelvin means the temperature rises by 3 K. With cooling, the temperature tends toward the point where incoming and outgoing heat balance. In the two-state model, heat lost from one state enters the other. Eight tests cover these calculations, heating and cooling, the zero-coupling limit, invalid inputs, and step refinement. Step refinement checks that the answer approaches the known result as the time intervals get shorter. [Numerical tests](../../tests/thermal/test_thermal_network.py)

## Fitting and validation

NumPy handles the temperature and input sequences as numeric arrays. SciPy's bounded least-squares routine adjusts the rates and gains to reduce prediction errors while keeping coefficients within their allowed ranges. Its recurrence routine advances the state efficiently over long recordings. Fitting used ten-second blocks formed from the 43 training sessions. Validation predictions used the original half-second inputs from 13 separate sessions. Every session resets the state, and the objective gives each training profile equal weight. [SciPy optimizer](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html) · [SciPy recurrence routine](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.lfilter.html)

The one-state candidate models the winding directly. The two-state candidate models an additional effective motor-body temperature and heat exchange between it and the winding. In the primary evaluation, both start from the first measured winding temperature, then predict all later winding temperatures from the admitted inputs and previous predicted state. The two-state model initializes its effective body halfway between coolant and initial winding temperature. A separately reported ambient-only startup uses ambient temperature for the initial winding. The fitting and prediction procedure follows the frozen profile split. [Input contract](../../qualification/thermal/motor-input-contract-1.0.0.json)

Mean absolute error averages the size of each error regardless of whether the prediction is high or low. The reported profile mean averages the per-profile scores equally, so long recordings do not dominate. The persistence reference keeps the startup temperature fixed throughout the recording. The accepted model must beat that reference. The study also reports signed error and error in high-current, lower-current, near-zero-speed, and negative shaft-power regimes. A sample can contribute to more than one regime.

The validation error distribution sets the band around the prediction. The report gives the fraction of recorded temperatures inside it. The band is empirical and was calibrated on this testbench. Its width matters alongside coverage. The training input ranges also show where a later sample falls outside the observed range of an individual input. Scoring includes those samples and reports their counts.

## What the state-estimation check measures

Open-loop prediction advances the model using the input signals and the previous predicted temperatures. A Kalman filter also uses a temperature measurement after each prediction. It weighs the measurement against the current prediction based on their expected uncertainty. In the synthetic experiment, both methods start from the same estimate and receive the same inputs. We score the next prediction before giving the filter the next temperature reading. The experiment repeats with sensor noise and with deliberately different heat and cooling parameters in the reference simulation.

With two thermally coupled states, the hidden body's temperature influences later winding readings. In the disconnected check, the winding carries information about only one state. The synthetic comparison establishes a result for its specified network and winding sensor. The current simulated bus supplies one modeled aggregate temperature, and the frozen bench protocol uses one measured winding value at startup. [Decision](../adr/0065-qualify-offline-winding-prediction.md)

## Reproduce the checks and inspect the reserved result

Open PowerShell in `<checkout>`. Use the audited ZIP from [P5-A1](motor-data-audit.md). These standalone analysis scripts used Python 3.13.7, NumPy 2.4.3, and SciPy 1.17.1 on Windows. They run outside the application environment. If those versions are missing, create an analysis environment in the ignored `.runtime` directory and install the recorded versions there. The commands below assume `python` points to that environment.

```powershell
$env:OPENBLAS_NUM_THREADS = '1'
python -m unittest discover -s tests/thermal -p 'test_thermal_network.py' -v
python scripts/thermal/qualify_bench_thermal.py develop --report evidence/p5/my-bench-development.json
python scripts/thermal/qualify_bench_thermal.py inspect
```

The thread setting gives the numeric library one worker for these small matrix operations. Expect eight passing tests and a development report with two validation scores, the selected candidate, and the synthetic observer result. Use a new report filename. The development command decodes only training and validation values. The reserved `inspect` command reports the state and location of the original result.

The `freeze` mode writes a release from passing development evidence. The `reserved` mode requires that exact release to be committed. It creates a permanent claim in the ignored evidence directory before decoding the 13 reserved profiles. A failure after that point leaves the claim for investigation. Preserve the original attempt and use `inspect` to read it. [Development evidence](../verification/bench-thermal-development.json)

## Measured results

Validation profile-mean error was 3.54 K for the one-state model and 3.42 K for the two-state candidate. The 0.11 K improvement fell short of the 0.15 K selection rule, so the release uses one state. Omitting two different sets of nine training sessions and refitting gave validation errors of 3.45 K and 3.62 K. This shows how the fitted model responds to changes in the training mix. The first synthetic observer run exposed an incorrect state-transition orientation. A regression test now checks the column-vector update against the exact trajectory. With model mismatch, the corrected observer reduced effective-body error from 4.13 K to 0.24 K on average across the three noise draws.

| Reserved test measure | Frozen limit or reference | Result |
|---|---:|---:|
| Mean absolute error averaged equally across 13 profiles | ≤5.31 K | 3.19 K |
| Worst profile mean absolute error | ≤15.00 K | 7.26 K |
| Mean profile coverage within the empirical ±17.70 K band | ≥85% | 99.99% |
| Mean error with startup temperature held fixed | Must beat this reference | 39.51 K reference versus 3.19 K model |

Scoring covered 268,299 predicted samples after the 13 initial readings. The measured-start worst individual miss was 17.98 K. Starting from ambient increased the mean error to 3.25 K and the largest miss to 26.38 K. The high-current regime had 6.47 K sample-weighted error and a 5.30 K average underprediction. The overall coverage reflects a broad empirical band, and the high-current bias matters when considering any later protection rule.

Per-feature counts outside training minima and maxima were 4 for ambient temperature, 2 for coolant temperature, 1 for speed, and 5 for squared current. These counts can overlap at the same sample. The qualified result covers winding prediction on this bench with the prescribed measured startup and observed input sequence. [Frozen release](../../qualification/thermal/bench-winding-release-1.0.0.json) · [Reserved results](../verification/bench-thermal.json)
