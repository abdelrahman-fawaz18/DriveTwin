"""Read-only geographic projection of an immutable forecast input."""

from dataclasses import asdict

from evdt.energy.route_aware.forecast import ForecastResult, validate_result
from evdt.energy.route_aware.geometry import remaining_route
from evdt.energy.route_aware.wire import ForecastRequest, decode_record, fingerprint


def project(request_payload, result_payload, fence, run, asset):
    request = decode_record(ForecastRequest, request_payload)
    result = decode_record(ForecastResult, result_payload)
    validate_result(request, result)
    if (request.state.scope.run_id, request.state.scope.asset_id) != (str(run), asset):
        raise ValueError("Forecast map source scope mismatch")
    route = remaining_route(request.route, request.state)

    def coordinate(point):
        if point.longitude_deg is None or point.latitude_deg is None:
            raise ValueError("Forecast geography is unavailable")
        return [point.longitude_deg, point.latitude_deg]

    coordinates = []
    stops = []
    for piece in route.pieces:
        if not coordinates:
            coordinates.append(coordinate(piece.start))
        coordinates.append(coordinate(piece.end))
        for stop in piece.stops_after:
            stops.append((stop, coordinate(piece.end)))
    if not coordinates:
        raise ValueError("No remaining geographic route")
    stops.extend((stop, coordinates[0]) for stop in route.initial_stops)
    features = [
        {
            "type": "Feature",
            "properties": {
                "id": "forecast-route",
                "layer": "route",
                "name": "Forecast remaining route",
            },
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }
    ]
    for stop, location in stops:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"{stop.reference.occurrence_index}:{stop.reference.stop_id}",
                    "layer": "stops",
                    "name": stop.reference.stop_id,
                    "duration_s": stop.dwell_working_s,
                },
                "geometry": {"type": "Point", "coordinates": location},
            }
        )
    return {
        "version": "forecast-map/1.0.0",
        "fence": fence,
        "input_sha256": fingerprint(request),
        "scope": asdict(request.state.scope),
        "cutoff_working_s": request.state.working_s,
        "observed_real_life_s": request.state.observed_real_life_s,
        "route_revision": request.state.route_revision,
        "context": {
            "context_version": "1.0.0",
            "type": "FeatureCollection",
            "run_id": str(run),
            "asset_id": asset,
            "mission_id": "Forecast remaining route",
            "attribution": "Route geometry from the saved forecast input",
            "input_sha256": {"forecast": fingerprint(request)},
            "bbox": [
                min(p[0] for p in coordinates),
                min(p[1] for p in coordinates),
                max(p[0] for p in coordinates),
                max(p[1] for p in coordinates),
            ],
            "features": features,
        },
    }
