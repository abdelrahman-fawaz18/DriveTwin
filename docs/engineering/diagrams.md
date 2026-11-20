# DriveTwin diagrams

Candidates for the GitHub landing page and final project documentation.

## 1. DriveTwin system map

SUMO, both databases, the Energy Forecast, the Charging Advisor, and the dashboard. Each arrow labels the interface and the data it carries. The map combines the monitoring stack with on-demand forecasting and the optional charging configuration.

```mermaid
flowchart TB
    subgraph Vehicle[Simulated vehicle and delivery]
        SUMO[SUMO] <-->|"TraCI · read bus and charger state, apply route and stop"| Adapter[Vehicle Adapter or Charging Fixture]
        Adapter -->|"SQLite file · append readings"| Queue[(SQLite outgoing queue)]
        Queue <-->|"SQLite file · read pending packets, retire confirmed packets"| Sender[Sender]
        Sender <-->|"MQTT · publish packets, receive storage receipts"| Broker[Mosquitto broker]
    end

    subgraph Twin[Twin storage and web service]
        Ingest[Ingestion] <-->|"SQL · check source, save readings and receipts"| PG[(PostgreSQL)]
        Registry[Registry] <-->|"SQL · read and save source identity and clock"| PG
        PG -->|"SQL read · readings, map context, and saved forecasts"| API[Monitoring API]
    end

    subgraph Forecast[Energy Forecast on demand]
        Controller[Forecast Controller] <-->|"Process input and output · JSON jobs and estimates"| Worker[Temporary Energy Workers]
        Controller -->|"Process input · assembled JSON forecast"| Writer[Forecast Writer]
    end

    subgraph Charge[Optional charging configuration]
        Command[Command API] <-->|"Python call · bus and charger state in, ranked stations out"| Advisor[Charging Advisor inside Command API]
        Operator[Operator] <-->|"HTTP · read recommendation, submit approval"| Command
    end

    Broker <-->|"MQTT · deliver packets, return storage receipts"| Ingest
    Adapter -->|"HTTP · register source and report clock state"| Registry
    API <-->|"HTTP · live readings, history, map, and saved forecasts"| Browser[Dashboard in browser]
    API <-->|"HTTP · request authorized readings, return forecast inputs"| Controller
    Writer <-->|"SQL · check input references, insert forecast"| PG
    Adapter <-->|"HTTP · send charging observations, claim authorized diversion"| Command
    Command <-->|"SQL · save and read charging observations, commands, and effects"| PG
```

## 2. SQLite read and write map

The Vehicle Adapter and Sender share the outgoing queue file. A double arrow means the program reads from and writes to SQLite.

```mermaid
flowchart LR
    A[Vehicle Adapter]
    Q[(SQLite queue file)]
    S[Sender]
    A <-->|Reads queue state · adds readings| Q
    Q <-->|Reads pending readings · retires confirmed packets| S
```

## 3. PostgreSQL read and write map

Arrows into PostgreSQL show writes, arrows out show reads, and double arrows show both. The forecast and command services appear when their configurations are enabled.

```mermaid
flowchart LR
    I[Ingestion] <-->|Reads source state · saves readings and receipts| D[(PostgreSQL)]
    R[Registry] <-->|Reads registrations · saves identity and clock state| D
    F[Forecast Writer · on demand] <-->|Reads input references · saves forecasts| D
    C[Command API · when enabled] <-->|Reads control state · saves commands and outcomes| D
    D -->|Current readings, history, map data, forecasts| M[Monitoring API]
    D -->|Recorded readings and playback context| P[Replay API]
    X[Migration jobs · at startup] -.->|Set up tables and permissions| D
```
