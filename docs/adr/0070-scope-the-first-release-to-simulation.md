# ADR 0070. Scope the first release to simulation

The maintainer approved a local simulation release on November 6, 2026. The release includes one-bus monitoring and bounded simulated control. A small two-asset exercise must demonstrate data and permission isolation. Earlier component studies support claims within their original scopes.

The ten-bus 30-minute run, fleet burst, fleet outage recovery, fleet queue-capacity outage, and fleet endurance run are separate fleet qualification work. Defer their execution and their corresponding performance and resource claims. Preserve the original workload definitions, measured short-run and one-bus passes, stopped attempts, and source fingerprints. The first release may finish without this fleet evidence. The release review must show the disposition of every original Q-01 through Q-12 gate under the revised profile.

Data accounting, access control, command authority, no duplicate effect, freshness, recovery, supported compatibility, and reproducibility remain required. Keep the existing host safeguards for new live checks. Use compatible retained evidence for earlier large history and restore measurements. Exercise affected behavior through the ordinary service paths. The optional bounded-storage profile stays off. Keep automatic charging within its previously authorized simulation scope. The default demo uses operator-controlled charging.

Hardware integration and optional adaptive calibration are outside the release scope. Complete applicable nonhardware prerequisites, including safe local startup, teardown, backup, restoration, dependency and data rights review, and a clean-start demo. Keep the development decisions and historical verification available as reference material. Organize the README, user guides, and release notes around capabilities and reproduction steps.

Save the verified package and Git commits locally. Publication requires separate authorization. [Release acceptance profile](../../qualification/release/simulation.json)
