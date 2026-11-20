# Motor test profile split and signal mapping

A profile is one motor testbench recording. The winding temperature at the end of a long drive depends on how the motor was used earlier in that drive. Splitting individual rows would put closely related measurements in both training and final testing. DriveTwin assigns the entire recording to one partition and resets the estimator at the next profile.

| Partition | Profiles | Samples | Recorded hours | Role |
|---|---:|---:|---:|---|
| Training | 43 | 795,471 | 110.48 | Fit model parameters and any input scaling. |
| Validation | 13 | 267,033 | 37.09 | Choose the model and set acceptance limits. |
| Reserved test | 13 | 268,312 | 37.27 | Score the frozen model once at P5-B4. |

The assignments are bound to the exact source archive. The hours use the publisher's declared 0.5-second sample period. The source CSV has row order and session IDs with no timestamps. [Frozen profile split](../../qualification/thermal/motor-profile-split-1.0.0.json) · [Source audit](motor-data-audit.md)

## How we grouped likely repeats

We aimed for 60% training, 20% validation, and 20% reserved testing by recorded duration. This gives long and short sessions appropriate weight when forming the split. The assignment uses speed, torque, and session lengths. Temperature outcomes are reserved for the subsequent model work.

For sessions within 2% of each other's length, compare their mean speed and torque in five-second blocks. Try offsets up to 10 seconds and require at least 95% overlap. Group sessions if their aligned average absolute differences are at most 75 rpm for speed and 5 newton-meters for torque. The last incomplete block is omitted from the comparison, while its samples count toward the partition's duration. These are screening thresholds for near-repeats, separate from the future model's accuracy limits.

The screen compared **64 pairs** and found three likely repeats. Profiles 46 and 47, 49 and 50, and 54 and 55 stayed together. All three groups landed in training. The assignment processes the longest groups first and chooses the partition with the lowest sample count relative to its target share. It uses a fixed order to break ties. Without publisher-provided session family labels, this screen has limited coverage of partial repeats and longer alignment shifts. Evaluation will measure generalization across reserved recordings from this bench. [Authors' validation scheme](https://arxiv.org/html/2103.16323#S3.SS1.SSS2)

## Which signals the model may use

| Quantity | Testbench prediction | Simulated bus |
|---|---|---|
| Environment and cooling | Ambient and coolant temperatures converted from Celsius to kelvin. Both describe where the motor can lose heat. | The base bus supplies assumed ambient temperature. The second bus adds a modeled coolant inlet. Its temperature describes a boundary condition. Pump response and coolant flow remain outside the available signals. |
| Shaft motion and load | Speed converted from revolutions per minute to radians per second. Torque describes the operating regime in the evaluation. | The bus computes equivalent speed and torque from simulated motion and power, including a low-speed torque approximation. Those are candidate bus inputs with their own assumptions. |
| Electrical heating input | Add the squares of the two measured motor current components. This captures current magnitude for a future heat term. A fitted conversion is required to express heat generation in watts. | The measured quantity in the existing catalog is simulated pack current. That current describes the whole battery pack and its loads. The bus lacks the testbench's motor-axis current input. |
| Motor temperature | Predict the stator winding temperature, measured at the stationary wire coil. Other internal temperatures remain outside this prediction contract. | One modeled aggregate motor temperature is available. It represents the motor as a whole. |

Initialize the primary bench run once with the first measured winding temperature. This models a startup with a winding sensor reading. An ambient-initialized run will show how performance changes when that reading is unavailable. Report the two initializations separately. Each input row advances the previous predicted temperature to the next sample. Score from the second sample onward and use the predicted temperature after initialization. The primary score therefore tests the evolution of the estimate through the drive. [Input contract](../../qualification/thermal/motor-input-contract-1.0.0.json)

The bus follows a separate simulation path with its own parameters and fault scenarios. Its one aggregate temperature cannot establish the real winding temperature of a bus motor. A model that estimates additional internal temperatures or uses measurements to correct its estimate will need a separate input rule and evidence at P5-B3. [Decision record](../adr/0064-separate-motor-testbench-and-bus-thermal-evidence.md)

## Verify the frozen split

From PowerShell in `<checkout>`, with the audited archive under `data/downloads/p5-motor/`, run:

```powershell
python scripts/thermal/freeze_motor_profile_split.py
```

The tool checks the source fingerprint, rebuilds the split in memory, and compares it with the committed manifest. Expect the three profile counts above and a line reporting 64 screened pairs and three grouped pairs. The command reads the original archive and leaves the manifest untouched. [Measured checkpoint record](../verification/motor-profile-split.json)

The [bench thermal qualification](bench-thermal-qualification.md) documents the completed numerical model, fitting, and reserved-session evaluation.
