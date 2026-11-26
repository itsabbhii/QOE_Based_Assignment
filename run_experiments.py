#!/usr/bin/env python3
"""Run 30-seed Base and Cases A-D experiments and generate paper-style Figs 6-11.

All outputs are explicitly model estimates based on the project's synthetic EV
coordinates and documented traffic approximations; they are not the authors'
original numerical results.
"""
from __future__ import annotations
import argparse, csv, random, statistics, json
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt

from paper_aligned_ga import (load_parameters, load_zones, load_stations,
    load_ports_and_adjacency, generate_ev_demand, PaperAlignedGA, DATA, OUT as BASE_OUT)
OUT = BASE_OUT

STATIONS = ["CS2", "CS3", "CS4", "CS6", "CS9", "CS1", "CS7", "CS8", "CS5", "CS10"]
CASES = ["base", "A", "B", "C", "D"]

class ScenarioGA(PaperAlignedGA):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fitness_cache = {}
        self.road_capacity_by_destination = {}
        if self.case == "a":
            # Paper Case A: double road capacity towards CS9 in NE3.
            self.road_capacity_by_destination["NE3"] = 2.0
        elif self.case == "b":
            # Paper Case B: reduce capacity towards NE1 stations by one third.
            self.road_capacity_by_destination["NE1"] = 2.0 / 3.0

    def fitness(self, chromosome):
        key = tuple(chromosome)
        if key not in self._fitness_cache:
            self._fitness_cache[key] = super().fitness(chromosome)
        return self._fitness_cache[key]

    def travel_time(self, i, u, assigned_count):
        ev, st = self.evs[i], self.stations[u]
        k, j = ev["zone"], st["zone"]
        road_cap_1 = self.road_capacity
        road_cap_2 = self.road_capacity * self.road_capacity_by_destination.get(j, 1.0)
        congestion_icev = self.icev_origin[(i, u)] / (road_cap_1 * self.phi)
        congestion_shared = (self.icev_destination[(i, u)] + assigned_count) / (
            road_cap_2 * self.phi * self.epsilon * self.sk * self.sj)
        delta = max(0.01, congestion_icev + congestion_shared)
        return self.ell * self.route_km[(i, u)] * delta


def load_scenario(case, seed):
    params = load_parameters()
    zones = load_zones()
    if case.lower() == "c":
        # Paper Case C: ICEVs sharing roads towards NE1 are raised to NE5 level.
        zones["NE1"]["icev_population"] = zones["NE5"]["icev_population"]
    adjacency, ports = load_ports_and_adjacency()
    stations = load_stations(params, case)
    evs = generate_ev_demand(zones, ports, seed=seed)
    return params, zones, adjacency, ports, stations, evs


def aggregate_run(rows, stations):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["station_id"]].append(row)
    out = {}
    for st in stations:
        sid = st["id"]
        rr = grouped[sid]
        out[sid] = {
            "assigned_evs": len(rr),
            "travel_min": sum(float(x["travel_min"]) for x in rr),
            "queue_min": sum(float(x["queue_min"]) for x in rr),
            "charge_min": sum(float(x["charge_min"]) for x in rr),
            "total_min": sum(float(x["total_min"]) for x in rr),
        }
    return out


def mean_sd(values):
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def run_case(case, seeds, generations, population, phi, epsilon):
    runs = []
    convergence = []
    station_rows = []
    for run_no, seed in enumerate(seeds, 1):
        params, zones, adjacency, ports, stations, evs = load_scenario(case, seed)
        ga = ScenarioGA(evs, stations, zones, adjacency, ports, params,
            population_size=population, generations=generations, seed=seed,
            case=case, phi=phi, epsilon=epsilon)
        best, history = ga.solve()
        objective, rows, feasible = ga.evaluate(best)
        if not feasible:
            raise RuntimeError(f"Infeasible solution: case={case}, seed={seed}")
        by_station = aggregate_run(rows, stations)
        runs.append({"case":case, "run":run_no, "seed":seed,
                     "ev_count":len(evs), "objective_ev_minutes":objective,
                     "average_completion_minutes":objective/len(evs),
                     "feasible":feasible})
        for sid, vals in by_station.items():
            station_rows.append({"case":case,"run":run_no,"seed":seed,
                                 "station_id":sid,**vals})
        for h in history:
            convergence.append({"case":case,"run":run_no,"seed":seed,**h})
        print(f"{case.upper():>4} run {run_no:02d}/{len(seeds)} seed={seed} objective={objective:.2f}")
    return runs, station_rows, convergence


def write_csv(path, rows):
    if not rows: return
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


def summarize(runs, station_rows):
    summary=[]
    for case in CASES:
        cr=[r for r in runs if r["case"]==case]
        obj=[r["objective_ev_minutes"] for r in cr]
        avg=[r["average_completion_minutes"] for r in cr]
        summary.append({"case":case,"runs":len(cr),"ev_count":cr[0]["ev_count"],
            "objective_mean":mean_sd(obj)[0],"objective_sd":mean_sd(obj)[1],
            "average_completion_mean":mean_sd(avg)[0],"average_completion_sd":mean_sd(avg)[1]})
    station_summary=[]
    for case in CASES:
        for sid in STATIONS:
            rr=[r for r in station_rows if r["case"]==case and r["station_id"]==sid]
            rec={"case":case,"station_id":sid}
            for metric in ["assigned_evs","travel_min","queue_min","charge_min","total_min"]:
                m,s=mean_sd([float(x[metric]) for x in rr])
                rec[metric+"_mean"]=m; rec[metric+"_sd"]=s
            station_summary.append(rec)
    return summary, station_summary


def plot_grouped(station_summary, metric, title, ylabel, filename):
    fig,ax=plt.subplots(figsize=(12,5.8)); x=list(range(len(STATIONS))); width=.15
    for idx,case in enumerate(CASES):
        vals=[next(r for r in station_summary if r["case"]==case and r["station_id"]==s)[metric+"_mean"] for s in STATIONS]
        errs=[next(r for r in station_summary if r["case"]==case and r["station_id"]==s)[metric+"_sd"] for s in STATIONS]
        pos=[v+(idx-2)*width for v in x]
        ax.bar(pos,vals,width,yerr=errs,capsize=2,label=("Base Scenario" if case=="base" else f"Case {case}"))
    ax.set_xticks(x); ax.set_xticklabels(STATIONS); ax.set_xlabel("Charging Stations")
    ax.set_ylabel(ylabel); ax.set_title(title); ax.legend(ncol=3); ax.grid(axis="y",alpha=.25)
    fig.tight_layout(); fig.savefig(OUT/filename,dpi=300); plt.close(fig)


def plot_fig11(summary):
    fig,ax=plt.subplots(figsize=(8.5,5.5))
    labels=["Base" if r["case"]=="base" else f"Case {r['case']}" for r in summary]
    ax.bar(labels,[r["objective_mean"] for r in summary],
           yerr=[r["objective_sd"] for r in summary],capsize=4)
    ax.set_ylabel("Total completion time (EV-minutes)")
    ax.set_title("Overall completion time: Base and Cases A-D")
    ax.grid(axis="y",alpha=.25); fig.tight_layout(); fig.savefig(OUT/"figure_11_overall_total_time.png",dpi=300); plt.close(fig)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--runs",type=int,default=30)
    ap.add_argument("--seed-start",type=int,default=1001)
    ap.add_argument("--output-suffix",default="")
    ap.add_argument("--generations",type=int,default=100)
    ap.add_argument("--population",type=int,default=32)
    ap.add_argument("--phi",type=float,default=.10)
    ap.add_argument("--epsilon",type=float,default=.10)
    args=ap.parse_args()
    global OUT
    if args.output_suffix:
        OUT = BASE_OUT / args.output_suffix
        OUT.mkdir(parents=True, exist_ok=True)
    seeds=list(range(args.seed_start,args.seed_start+args.runs))
    all_runs=[]; all_station=[]; all_conv=[]
    # Cases are independent; parallelize at case level while keeping each case's
    # seeds deterministic and ordered in the saved CSVs.
    with ProcessPoolExecutor(max_workers=min(len(CASES), 5)) as pool:
        futures={pool.submit(run_case,case,seeds,args.generations,args.population,args.phi,args.epsilon):case for case in CASES}
        results={}
        for future in as_completed(futures):
            case=futures[future]
            results[case]=future.result()
            print(f"Finished {case}",flush=True)
    for case in CASES:
        runs,stations,conv=results[case]
        all_runs+=runs; all_station+=stations; all_conv+=conv
    write_csv(OUT/"experiment_runs_base_A_D.csv",all_runs)
    write_csv(OUT/"experiment_station_runs_base_A_D.csv",all_station)
    write_csv(OUT/"experiment_convergence_base_A_D.csv",all_conv)
    summary,station_summary=summarize(all_runs,all_station)
    write_csv(OUT/"experiment_summary_base_A_D.csv",summary)
    write_csv(OUT/"experiment_station_summary_base_A_D.csv",station_summary)
    plot_grouped(station_summary,"assigned_evs","EV assignments by charging station","Mean number of assigned EVs","figure_06_assignments.png")
    plot_grouped(station_summary,"travel_min","Travel time by charging station","Sum of travel time (EV-minutes)","figure_07_travel_time.png")
    plot_grouped(station_summary,"queue_min","Queue time by charging station","Sum of queue time (EV-minutes)","figure_08_queue_time.png")
    plot_grouped(station_summary,"charge_min","Charging time by charging station","Sum of charging time (EV-minutes)","figure_09_charging_time.png")
    plot_grouped(station_summary,"total_min","Total completion time by charging station","Sum of completion time (EV-minutes)","figure_10_total_time_station.png")
    plot_fig11(summary)
    metadata={"runs":args.runs,"seeds":seeds,"population":args.population,"generations":args.generations,
              "phi":args.phi,"epsilon":args.epsilon,
              "coordinate_model":"synthetic jitter around published zone-port anchors",
              "traffic_model":"zone-level ICEV approximation; not road-segment measurements",
              "queue_model":"existing project Eq.7 approximation with lambda_u=48",
              "status":"paper-structured approximation, not exact numerical replication"}
    (OUT/"experiment_metadata_base_A_D.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    print("\nCompleted Base and Cases A-D; figures 6-11 and CSV summaries saved in",OUT)

if __name__=="__main__": main()
