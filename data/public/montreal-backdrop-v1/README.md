# Central Montréal map backdrop

© OpenStreetMap contributors. This filtered geographic database is licensed under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Keep the attribution and [OSM copyright notice](https://www.openstreetmap.org/copyright) with redistributed maps and data.

`backdrop.json` contains road geometry and names, parks, water, and place labels from the retained Montréal BBBike extract. It supplies the dashboard's local geographic background. Vehicle positions, trajectories, and forecast routes come from authenticated monitoring interfaces.

`source.json` records the source fingerprints and geographic filtering. The background is a fixed snapshot; it contains no live traffic or vehicle observations. Outside its coverage the dashboard uses the road geometry supplied by the deployment's map context.
