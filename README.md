# EV Charging Station Assignment Using Genetic Algorithm
## Project Guide and Execution Manual

### 1. Project overview

This project implements a Genetic Algorithm (GA)-based prototype for assigning electric vehicles (EVs) to charging stations (CSs). Its objective is to reduce the total time experienced by EV users:

**Total completion time = Travel time + Queue time + Charging time**

The project is based on the framework described in:

Aljaidi et al., “QoE-Based Assignment of EVs to Charging Stations in Metropolitan Environments,” *IEEE Transactions on Intelligent Vehicles*, Vol. 10, No. 1, January 2025. DOI: 10.1109/TIV.2024.3412372.

> **Research note:** This is a paper-aligned implementation, not an exact reproduction of the authors' original experiment. The paper does not publish all individual EV coordinates and road-level traffic inputs needed for exact numerical replication. The assumptions and approximations used here are identified below.

---

### 2. Project folder structure

Keep the folder structure unchanged after extracting the ZIP file:

```text
ev_qoe_paper_aligned_project/
│
├── paper_aligned_ga.py
├── README.md
├── README_Project_Guide.md
│
├── data/
│   ├── ga_parameters.csv
│   ├── zones.csv
│   ├── charging_stations.csv
│   ├── zone_adjacency_ports.csv
│   ├── study_area_corners.csv
│   └── case_study_overrides.csv
│
└── results/
    ├── assignments_base.csv
    ├── station_summary_base.csv
    ├── convergence_base.csv
    └── ev_requests_synthetic.csv
```

Do not move the CSV files out of the `data` folder. The Python program reads them from this location.

---

### 3. Software requirements

- Python 3.9 or later
- No third-party Python packages are required
- Windows, Linux, or macOS

Check Python installation:

```bash
python --version
```

If your system uses the `python3` command, use that instead of `python`.

---

### 4. Input dataset description

| File | Purpose |
|---|---|
| `ga_parameters.csv` | Model and optimization parameters, including N, M, Z and QoE threshold |
| `zones.csv` | Zone-wise EV and ICEV population and derived peak-hour EV demand |
| `charging_stations.csv` | Charging-station IDs, zone, coordinates, charger count and service rate |
| `zone_adjacency_ports.csv` | Adjacent-zone relationships and boundary-port coordinates |
| `study_area_corners.csv` | Coordinates of the four corners of the study area |
| `case_study_overrides.csv` | Changes used in the paper's case studies |

#### Main base-scenario values

| Parameter | Value | Meaning |
|---|---:|---|
| N | 415 | EVs requiring charging in the peak-hour scenario |
| M | 10 | Charging stations |
| Z | 7 | Postcode zones |
| ηu | 10 | Chargers at each station in the base scenario |
| ru | 6 | EVs served per charger per hour |
| Γ | 120 min | Maximum QoE completion-time threshold |
| ζ | 1590 | Road-capacity parameter listed in the paper |
| λu | 48 | Parameter listed in the paper and used by this implementation in its queue calculation |

The peak-hour demand is derived by applying the paper's 10% charging-demand assumption to the zone EV populations. It is not a list of 415 measured individual EV records.

---

### 5. How the Genetic Algorithm works

The chromosome represents an assignment of every EV to one charging station. Each gene stores the selected station for one EV.

The program follows this general GA process:

1. **Initialize population:** Generate candidate assignments for the EV requests.
2. **Evaluate fitness:** Calculate travel, queue and charging time for each assignment.
3. **Selection:** Select better candidate solutions using tournament selection.
4. **Crossover:** Combine two parent assignments to create offspring.
5. **Mutation:** Randomly change selected EV-to-station assignments.
6. **Repair:** Enforce station capacity and adjacency eligibility.
7. **Elitism:** Preserve the best solution between generations.
8. **Termination:** Stop after the configured number of generations and return the best candidate.

The objective is to minimize the sum of EV completion times.

#### Constraints represented in the implementation

- Each EV is assigned to one charging station.
- An EV can select a station only in an adjacent zone.
- An EV cannot select a station in its own zone.
- Station assignments cannot exceed the station's charger-service capacity.
- Each EV's calculated completion time is checked against the QoE threshold Γ.

---

### 6. Time calculations

The implementation uses the paper's model structure:

- **Travel time:** based on route distance and congestion.
- **Queue time:** depends on the number of EVs assigned to a station and station service parameters.
- **Charging time:** calculated as `60 / ru` minutes.
- **Total completion time:** travel time + queue time + charging time.

For the base scenario, `ru = 6`, so the charging time calculated by the program is:

```text
60 / 6 = 10 minutes
```

The route is modelled in two legs:

```text
EV location → boundary port → charging station
```

Distances are calculated using the Haversine formula.

---

### 7. How to run the project

#### Step 1: Extract the ZIP

Extract the complete project ZIP file.

#### Step 2: Open the project folder

Open a terminal or command prompt inside:

```text
ev_qoe_paper_aligned_project
```

#### Step 3: Run the base scenario

```bash
python paper_aligned_ga.py
```

#### Step 4: Run Case D

In Case D, the service rate for CS1 and CS7 is changed to 8 EVs per charger per hour:

```bash
python paper_aligned_ga.py --case D
```

#### Step 5: Change GA settings

Example with a population size of 60, 400 generations and random seed 21:

```bash
python paper_aligned_ga.py --population 60 --generations 400 --seed 21
```

The same seed makes the synthetic EV coordinates and random GA operations repeatable.

---

### 8. Output files

After execution, the program writes result files into the `results/` folder.

| Output file | Description |
|---|---|
| `assignments_base.csv` | Selected charging station and time breakdown for each EV |
| `station_summary_base.csv` | Number of EVs assigned to each station and its queue/charging information |
| `convergence_base.csv` | Best objective value recorded at each GA generation |
| `ev_requests_synthetic.csv` | Generated EV coordinates, with a column identifying them as synthetic |

When Case D is run, the corresponding result filenames use the `_d` suffix for assignment, station summary and convergence files.

---

### 9. Important research limitations

The paper describes the model and provides zone, station and adjacency information, but does not provide every raw input needed to reproduce the original experiment exactly.

This implementation therefore makes the following explicit approximations:

1. **EV coordinates:** The paper does not publish the individual GPS coordinates of all 415 EV requests. The program generates repeatable illustrative coordinates near zone anchors derived from published port coordinates. These are synthetic, not original Newcastle EV traces.
2. **Road-level traffic:** The paper does not provide complete road-segment ICEV counts and capacities for every EV route. Zone-level ICEV population is used to approximate route-level traffic.
3. **Traffic distribution:** The 10% shared-road ICEV estimate is distributed across adjacent-zone links as an implementation assumption. This distribution is not a measured value supplied by the paper.
4. **Queue parameter:** The paper lists `λu = 48` in its parameter table but does not provide a complete station-specific charging-rate dataset. This implementation uses that value as the charging-rate factor in the queue denominator. This interpretation should be verified against the authors' implementation before claiming exact replication.
5. **Numerical results:** Because the missing source inputs are approximated, the generated assignment and objective values should not be presented as the exact results reported by the paper.

For a rigorous reproduction, obtain the original EV coordinates, zone boundaries, route-level traffic counts, road capacities and complete station charging parameters.

---

### 10. Suggested methodology for a research report

When documenting this implementation, clearly separate:

- **Paper-derived data:** zone populations, charging-station locations, adjacency/port coordinates and stated model parameters.
- **Derived data:** peak-hour EV demand obtained from the 10% assumption.
- **Implementation assumptions:** synthetic EV coordinates and approximate route-level traffic distribution.
- **Algorithm settings:** population size, number of generations, mutation/crossover settings and random seed.
- **Results:** total objective, average completion time, station-wise assignments and convergence history.

For stronger evaluation, run the GA multiple times with different random seeds and report the mean and standard deviation. The paper reports averaging results over 30 independent runs; matching that evaluation practice does not by itself make the input data identical.

---

### 11. Troubleshooting

**Error: CSV file not found**

- Confirm that the `data` folder is beside `paper_aligned_ga.py`.
- Do not rename the CSV files.
- Run the command from the extracted project folder.

**Error: Python command not found**

- Install Python 3.9 or later.
- On some systems, use `python3 paper_aligned_ga.py`.

**Want to repeat the same run**

Use the same `--seed`, `--population` and `--generations` values.

**Want to run the paper's Case D**

```bash
python paper_aligned_ga.py --case D
```

---

### 12. Final note

This project is intended for learning, experimentation and further research on QoE-based EV charging-station assignment using Genetic Algorithms. Always disclose the synthetic coordinates and traffic approximations when presenting the results.

---

## 13. Experimental runner: Base and Cases A-D (Figures 6-11)

The additional script `run_experiments.py` implements the first experimental batch and produces paper-structured Figures 6-11:

- Figure 6: mean station-wise EV assignments.
- Figure 7: mean station-wise sum of travel time.
- Figure 8: mean station-wise sum of queue time.
- Figure 9: mean station-wise sum of charging time.
- Figure 10: mean station-wise sum of total completion time.
- Figure 11: overall total completion time for Base and Cases A-D.

It runs independent seeds and reports sample standard deviations as error bars. Example:

```bash
python -m pip install matplotlib
python run_experiments.py --runs 30 --seed-start 1001 --generations 20 --population 16
```

The included 30-run results were executed using seeds 1001-1030, population 16 and 20 generations. These settings were selected to complete the multi-run evaluation in the available compute environment; they are lower than the original single-run script defaults (population 32, 100 generations). For a convergence/sensitivity study, increase these settings and rerun. The runner writes its CSV data, metadata and PNG figures to `results/`.

Scenario modelling in the runner:

- A: destination-road capacity factor for NE3 (CS9) is multiplied by 2.
- B: destination-road capacity factor for NE1 is multiplied by 2/3.
- C: the NE1 ICEV population input is set equal to the NE5 ICEV population input.
- D: the existing station loader changes the per-charger rate at CS1 and CS7 from 6 to 8.

**Interpretation warning:** The model retains the source project's synthetic EV coordinates, zone-level traffic approximation and queue-equation approximation (`lambda_u=48`). These outputs are experimental estimates under those assumptions, not the authors' original numerical results. Figures 12-16 (Case E and QoE-versus-Greedy) are not yet included in this milestone.
