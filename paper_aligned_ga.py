#!/usr/bin/env python3
from __future__ import annotations
import argparse
import csv
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)

EARTH_RADIUS_KM = 6371.0


def read_csv(name):
    with open(DATA / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def load_parameters():
    rows = read_csv("ga_parameters.csv")
    p = {r["parameter"]: float(r["value"]) for r in rows}
    return p


def load_zones():
    rows = read_csv("zones.csv")
    result = {}
    for r in rows:
        result[r["zone_id"]] = {
            "ev_population": int(r["ev_population_paper"]),
            "icev_population": int(r["icev_population_paper"]),
            "peak_ev_demand": int(r["peak_hour_ev_demand_derived"]),
        }
    return result


def load_stations(params, case="base"):
    rows = read_csv("charging_stations.csv")
    stations = []
    for r in rows:
        sid = r["station_id"]
        ru = float(r["evs_per_charger_per_hour_r_u"])
        if case.lower() == "d" and sid in {"CS1", "CS7"}:
            ru = 8.0
        stations.append({
            "id": sid,
            "zone": r["zone_id"],
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "chargers": float(r["chargers_eta_u"]),
            "r": ru,
        })
    return stations


def load_ports_and_adjacency():
    rows = read_csv("zone_adjacency_ports.csv")
    adjacency = defaultdict(list)
    ports = {}
    for r in rows:
        k, j = r["origin_zone_k"], r["adjacent_destination_zone_j"]
        point = (float(r["port_latitude"]), float(r["port_longitude"]))
        adjacency[k].append(j)
        ports[(k, j)] = point
    return dict(adjacency), ports


def generate_ev_demand(zones, ports, seed=2025):
    """
    Create the 415 peak-hour EV requests. Counts are paper-derived by
    rounding 10% of each zone's Table-IV EV population.
    Individual GPS points are NOT supplied by the paper: we create
    deterministic synthetic points near each origin zone's mean port
    coordinate, with small jitter. Replace this function with real EV
    coordinates when available.
    """
    rng = random.Random(seed)
    zone_anchor = {}
    for z in zones:
        pts = [pt for (origin, _), pt in ports.items() if origin == z]
        if not pts:
            raise ValueError(f"No published port coordinates for {z}")
        zone_anchor[z] = (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )

    evs = []
    for z, info in zones.items():
        for n in range(info["peak_ev_demand"]):
            lat0, lon0 = zone_anchor[z]
            # Small illustrative jitter around zone anchor; not paper GPS.
            lat = lat0 + rng.uniform(-0.004, 0.004)
            lon = lon0 + rng.uniform(-0.006, 0.006)
            evs.append({
                "id": f"EV{len(evs)+1:03d}",
                "zone": z,
                "lat": lat,
                "lon": lon,
                "coordinate_source": "synthetic_jitter_around_published_port_anchor",
            })
    return evs


class PaperAlignedGA:
    def __init__(self, evs, stations, zones, adjacency, ports, params,
                 population_size=48, generations=250, seed=7,
                 case="base", phi=0.10, epsilon=0.10,
                 traffic_flow_origin=1.0, traffic_flow_destination=1.0,
                 charging_rate_lambda=None):
        self.evs = evs
        self.stations = stations
        self.zones = zones
        self.adjacency = adjacency
        self.ports = ports
        self.params = params
        self.rng = random.Random(seed)
        self.pop_size = max(10, population_size)
        self.generations = max(1, generations)
        self.case = case.lower()

        self.ell = params.get("ell", 0.2)
        self.road_capacity = params.get("zeta", 1590.0)
        self.gamma = params.get("Gamma", 120.0)
        self.phi = phi
        self.epsilon = epsilon
        self.sk = traffic_flow_origin
        self.sj = traffic_flow_destination
        # The paper's Table-V lambda_u=48 is used as the charging-rate
        # factor in the queue denominator; this interpretation is exposed.
        self.lambda_u = charging_rate_lambda or params.get("lambda_u", 48.0)

        self.allowed = []
        for ev in evs:
            eligible_zones = set(adjacency.get(ev["zone"], []))
            choices = [i for i, s in enumerate(stations) if s["zone"] in eligible_zones]
            if not choices:
                raise ValueError(f"{ev['id']} in {ev['zone']} has no eligible adjacent-zone station")
            self.allowed.append(choices)

        self.capacity = [
            int(s["chargers"] * s["r"]) for s in stations
        ]
        if sum(self.capacity) < len(evs):
            raise ValueError("Total station capacity is below the number of EV requests.")

        # Precompute route distance and the zone-specific ICEV term for each EV/station.
        self.route_km = {}
        self.icev_origin = {}
        self.icev_destination = {}
        for i, ev in enumerate(evs):
            k = ev["zone"]
            for u in self.allowed[i]:
                st = stations[u]
                j = st["zone"]
                port = ports[(k, j)]
                d1 = haversine_km(ev["lat"], ev["lon"], port[0], port[1])
                d2 = haversine_km(port[0], port[1], st["lat"], st["lon"])
                self.route_km[(i, u)] = d1 + d2
                
                self.icev_origin[(i, u)] = (
                    zones[k]["icev_population"] * 0.10 /
                    max(1, len(adjacency.get(k, [])))
                )
                self.icev_destination[(i, u)] = (
                    zones[j]["icev_population"] * 0.10 /
                    max(1, len(adjacency.get(j, [])))
                )

    def travel_time(self, i, u, assigned_count):
        ev = self.evs[i]
        st = self.stations[u]
        k, j = ev["zone"], st["zone"]

       
        road_cap_1 = self.road_capacity
        road_cap_2 = self.road_capacity
        congestion_icev = self.icev_origin[(i, u)] / (road_cap_1 * self.phi)
        congestion_shared = (
            self.icev_destination[(i, u)] + assigned_count
        ) / (
            road_cap_2 * self.phi * self.epsilon * self.sk * self.sj
        )
        delta = max(0.01, congestion_icev + congestion_shared)

       
        return self.ell * self.route_km[(i, u)] * delta

    def station_times(self, chromosome):
        counts = Counter(chromosome)
        q, rho = [], []
        for u, st in enumerate(self.stations):
           
            denom = (
                st["chargers"] * st["r"] * self.epsilon * self.lambda_u
            )
            
            q.append(60.0 * counts[u] / denom if denom else float("inf"))
            # Paper Eq. (8).
            rho.append(60.0 / st["r"])
        return counts, q, rho

    def evaluate(self, chromosome):
        counts, queues, charges = self.station_times(chromosome)
        rows = []
        total = 0.0
        feasible = True

        for i, u in enumerate(chromosome):
            if u not in self.allowed[i]:
                feasible = False
                continue
            travel = self.travel_time(i, u, counts[u])
            queue = queues[u]
            charge = charges[u]
            total_time = travel + queue + charge
            if total_time > self.gamma:
                feasible = False
            total += total_time
            rows.append({
                "ev_id": self.evs[i]["id"],
                "origin_zone": self.evs[i]["zone"],
                "station_id": self.stations[u]["id"],
                "station_zone": self.stations[u]["zone"],
                "travel_min": travel,
                "queue_min": queue,
                "charge_min": charge,
                "total_min": total_time,
            })

        if any(counts[u] > self.capacity[u] for u in range(len(self.stations))):
            feasible = False
        return total, rows, feasible

    def fitness(self, chromosome):
        total, _, feasible = self.evaluate(chromosome)
        return total if feasible else total + 1e8

    def make_individual(self):
      
        slots = []
        for u, cap in enumerate(self.capacity):
            slots.extend([u] * cap)
        order = list(range(len(self.evs)))
        self.rng.shuffle(order)
        # Process the most constrained EVs first.
        order.sort(key=lambda i: len(self.allowed[i]))
        slot_owner = [-1] * len(slots)
        ev_slot = [-1] * len(self.evs)
        station_slots = defaultdict(list)
        for q, station_index in enumerate(slots):
            station_slots[station_index].append(q)

        def augment(ev_i, seen):
            station_order = self.allowed[ev_i][:]
            self.rng.shuffle(station_order)
            station_order.sort(key=lambda u: self.route_km[(ev_i, u)])
            for u in station_order:
                candidates = [q for q in station_slots[u] if q not in seen]
                self.rng.shuffle(candidates)
                for q in candidates:
                    seen.add(q)
                    previous = slot_owner[q]
                    if previous == -1 or augment(previous, seen):
                        slot_owner[q] = ev_i
                        ev_slot[ev_i] = q
                        return True
            return False

        for ev_i in order:
            if not augment(ev_i, set()):
                raise ValueError(
                    "No feasible assignment under current adjacency and capacity. "
                    "Check extracted adjacency/EV demand/capacity inputs."
                )
        return [slots[ev_slot[i]] for i in range(len(self.evs))]

    def repair(self, chrom):
        counts = Counter(chrom)
        # All genes are kept in eligible adjacent-zone station lists.
        for i, u in enumerate(chrom):
            if u not in self.allowed[i]:
                candidates = [x for x in self.allowed[i] if counts[x] < self.capacity[x]]
                if candidates:
                    chrom[i] = min(candidates, key=lambda x: self.route_km[(i, x)])
                    counts[chrom[i]] += 1
        # Move excess EVs from overloaded stations to available eligible stations.
        for u in range(len(self.stations)):
            while counts[u] > self.capacity[u]:
                owners = [i for i, x in enumerate(chrom) if x == u]
                self.rng.shuffle(owners)
                moved = False
                for i in owners:
                    alternatives = [x for x in self.allowed[i]
                                    if x != u and counts[x] < self.capacity[x]]
                    if alternatives:
                        v = min(alternatives, key=lambda x: self.route_km[(i, x)])
                        chrom[i] = v
                        counts[u] -= 1
                        counts[v] += 1
                        moved = True
                        break
                if not moved:
                    break
        return chrom

    def tournament(self, pop, k=3):
        contestants = self.rng.sample(pop, min(k, len(pop)))
        return min(contestants, key=self.fitness)[:]

    def solve(self):
        pop = [self.make_individual() for _ in range(self.pop_size)]
        best = min(pop, key=self.fitness)[:]
        history = []
        for generation in range(self.generations):
            new_pop = [best[:]]
            while len(new_pop) < self.pop_size:
                p1, p2 = self.tournament(pop), self.tournament(pop)
                c1, c2 = p1[:], p2[:]
                if self.rng.random() < 0.85 and len(c1) > 1:
                    cut = self.rng.randint(1, len(c1)-1)
                    c1, c2 = p1[:cut] + p2[cut:], p2[:cut] + p1[cut:]
                for child in (c1, c2):
                    for i in range(len(child)):
                        if self.rng.random() < 0.04:
                            child[i] = self.rng.choice(self.allowed[i])
                    child = self.repair(child)
                    new_pop.append(child)
                    if len(new_pop) >= self.pop_size:
                        break
            pop = new_pop[:self.pop_size]
            candidate = min(pop, key=self.fitness)
            if self.fitness(candidate) < self.fitness(best):
                best = candidate[:]
            history.append({"generation": generation+1, "best_total_minutes": self.fitness(best)})
        return best, history


def main():
    parser = argparse.ArgumentParser(description="Paper-aligned QoE EV-to-CS Genetic Algorithm")
    parser.add_argument("--case", choices=["base", "D"], default="base",
                        help="base scenario or paper Case D (CS1 and CS7 r=8)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--generations", type=int, default=100)
    parser.add_argument("--population", type=int, default=32)
    parser.add_argument("--phi", type=float, default=0.10,
                        help="ICEV shared-road proportion; paper assumes 10%%")
    parser.add_argument("--epsilon", type=float, default=0.10,
                        help="EV peak-hour charging proportion; paper assumes 10%%")
    args = parser.parse_args()

    params = load_parameters()
    zones = load_zones()
    adjacency, ports = load_ports_and_adjacency()
    stations = load_stations(params, args.case)
    evs = generate_ev_demand(zones, ports, seed=args.seed)

    ga = PaperAlignedGA(
        evs, stations, zones, adjacency, ports, params,
        population_size=args.population,
        generations=args.generations,
        seed=args.seed,
        case=args.case,
        phi=args.phi,
        epsilon=args.epsilon,
    )
    best, history = ga.solve()
    objective, rows, feasible = ga.evaluate(best)

    
    with open(OUT / "ev_requests_synthetic.csv", "w", newline="", encoding="utf-8-sig") as f:
        fields = ["ev_id", "origin_zone", "latitude", "longitude", "coordinate_source"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for ev in evs:
            w.writerow({
                "ev_id": ev["id"], "origin_zone": ev["zone"],
                "latitude": ev["lat"], "longitude": ev["lon"],
                "coordinate_source": ev["coordinate_source"],
            })

    # Assignment output (all 415 EVs)
    out_csv = OUT / f"assignments_{args.case.lower()}.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Station summary
    summary = []
    for st in stations:
        sr = [r for r in rows if r["station_id"] == st["id"]]
        summary.append({
            "station_id": st["id"],
            "zone": st["zone"],
            "assigned_evs": len(sr),
            "chargers": st["chargers"],
            "r_evs_per_charger_hour": st["r"],
            "capacity_evs_per_peak_hour": int(st["chargers"]*st["r"]),
            "queue_minutes": sr[0]["queue_min"] if sr else 0.0,
            "charge_minutes": sr[0]["charge_min"] if sr else 60/st["r"],
        })
    with open(OUT / f"station_summary_{args.case.lower()}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    with open(OUT / f"convergence_{args.case.lower()}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["generation", "best_total_minutes"])
        w.writeheader()
        w.writerows(history)

    print(f"Scenario: {args.case}")
    print(f"EV requests: {len(evs)} | Stations: {len(stations)} | Zones: {len(zones)}")
    print(f"Feasible solution: {feasible}")
    print(f"Total objective (sum of per-EV completion times): {objective:.2f} EV-minutes")
    print(f"Average completion time: {objective/len(evs):.2f} minutes")
    print("Station assignments:")
    for s in summary:
        print(f"  {s['station_id']} ({s['zone']}): {s['assigned_evs']} EVs; "
              f"queue={s['queue_minutes']:.2f} min; charge={s['charge_minutes']:.2f} min")
    print(f"Saved: {out_csv}")
    print(f"Saved: {OUT / f'station_summary_{args.case.lower()}.csv'}")
    print(f"Saved: {OUT / f'convergence_{args.case.lower()}.csv'}")


if __name__ == "__main__":
    main()
