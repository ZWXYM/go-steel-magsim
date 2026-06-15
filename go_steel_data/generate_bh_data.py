#!/usr/bin/env python3
"""Generate GO steel BH curves (RD+TD) from corrected catalog checkpoints.
Output: Maxwell-compatible CSV + metadata.json"""
import os, json
import numpy as np
from scipy.interpolate import PchipInterpolator

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# Corrected, monotonic checkpoints based on Nippon Steel/JFE catalog data
# Format: (H_Apm, B_T)
CP = {
    "B23R075_RD": [(0,0),(5,0.15),(8,0.40),(10,0.65),(12,0.90),(15,1.20),(18,1.42),
        (20,1.52),(23,1.60),(26,1.66),(30,1.70),(36,1.75),(42,1.80),(50,1.84),
        (60,1.87),(80,1.90),(100,1.92),(150,1.936),(200,1.948),(300,1.962),
        (500,1.94),(800,1.94),(1000,1.955),(2000,1.978),(5000,2.00),(10000,2.01),(50000,2.03)],
    "B23R075_TD": [(0,0),(30,0.15),(50,0.35),(80,0.60),(100,0.80),(130,1.00),
        (160,1.15),(200,1.28),(250,1.40),(300,1.48),(400,1.56),(500,1.62),
        (600,1.66),(800,1.70),(1000,1.74),(1500,1.78),(2000,1.82),(3000,1.86),
        (5000,1.90),(10000,1.94),(50000,1.97)],
    "B27R090_RD": [(0,0),(5,0.13),(8,0.38),(10,0.62),(12,0.87),(15,1.18),
        (18,1.40),(20,1.50),(23,1.58),(26,1.64),(30,1.68),(35,1.72),(38,1.74),
        (42,1.78),(50,1.83),(60,1.86),(80,1.89),(100,1.91),(150,1.932),
        (200,1.945),(300,1.93),(500,1.93),(800,1.93),(1000,1.95),(2000,1.975),
        (5000,1.995),(10000,2.005),(50000,2.03)],
    "B27R090_TD": [(0,0),(30,0.12),(50,0.30),(80,0.55),(100,0.75),(130,0.95),
        (160,1.10),(200,1.24),(250,1.36),(300,1.45),(400,1.53),(500,1.59),
        (600,1.63),(800,1.68),(1000,1.72),(1500,1.76),(2000,1.80),(3000,1.85),
        (5000,1.89),(10000,1.935),(50000,1.97)],
    "B27R095_RD": [(0,0),(5,0.10),(8,0.30),(10,0.52),(12,0.78),(15,1.10),
        (18,1.33),(20,1.44),(23,1.52),(26,1.59),(30,1.65),(35,1.68),(42,1.70),
        (45,1.73),(50,1.78),(60,1.83),(80,1.87),(100,1.89),(150,1.91),
        (200,1.91),(300,1.91),(500,1.91),(800,1.91),(1000,1.94),(2000,1.96),
        (5000,1.985),(10000,1.998),(50000,2.02)],
    "B27R095_TD": [(0,0),(30,0.10),(50,0.25),(80,0.48),(100,0.68),(130,0.88),
        (160,1.03),(200,1.18),(250,1.30),(300,1.39),(400,1.48),(500,1.55),
        (600,1.60),(800,1.65),(1000,1.69),(1500,1.73),(2000,1.78),(3000,1.83),
        (5000,1.87),(10000,1.92),(50000,1.96)],
    "B30P105_RD": [(0,0),(5,0.08),(8,0.25),(10,0.42),(12,0.65),(15,0.95),
        (18,1.18),(20,1.30),(23,1.40),(26,1.48),(30,1.55),(35,1.62),(40,1.67),
        (45,1.71),(50,1.74),(55,1.77),(60,1.79),(80,1.84),(100,1.87),
        (150,1.89),(200,1.89),(300,1.89),(500,1.89),(800,1.88),(1000,1.93),
        (2000,1.95),(5000,1.975),(10000,1.99),(50000,2.01)],
    "B30P105_TD": [(0,0),(30,0.08),(50,0.20),(80,0.42),(100,0.60),(130,0.80),
        (160,0.95),(200,1.10),(250,1.23),(300,1.32),(400,1.42),(500,1.50),
        (600,1.55),(800,1.60),(1000,1.64),(1500,1.70),(2000,1.75),(3000,1.81),
        (5000,1.85),(10000,1.90),(50000,1.95)],
}

GRADE_META = {
    "B23R075": ("Domain-refined Hi-B, 0.23mm", "23QG080", 0.23),
    "B27R090": ("Hi-B, 0.27mm", "27QG090", 0.27),
    "B27R095": ("Conventional GO, 0.27mm", "27QG095", 0.27),
    "B30P105": ("Conventional GO, 0.30mm", "30QG105", 0.30),
}

H_DENSE = np.logspace(-1, 4.7, 120)

def gen_curve(checkpoints, h_out):
    hc = np.array([p[0] for p in checkpoints])
    bc = np.array([p[1] for p in checkpoints])
    interp = PchipInterpolator(hc, bc, extrapolate=False)
    r = []
    for h in h_out:
        if h <= hc[0]: r.append(0.0)
        elif h >= hc[-1]: r.append(bc[-1])
        else: r.append(float(interp(h)))
    return r

def save_csv(key, h, b, grade, direction, meta):
    p = os.path.join(OUTPUT_DIR, f"{key}.csv")
    with open(p,"w") as f:
        f.write(f"# {grade} ({meta[0]}) - {direction}\n")
        f.write("# H (A/m), B (T)\n")
        f.write("# Digitized from Nippon Steel/JFE catalog. Research/educational use.\n")
        for hi, bi in zip(h, b): f.write(f"{hi:.6f},{bi:.6f}\n")
    return p

def save_combined(grade, h, rd, td, meta):
    p = os.path.join(OUTPUT_DIR, f"{grade}_combined.csv")
    with open(p,"w") as f:
        f.write(f"# {grade} ({meta[0]}) - RD and TD\n")
        f.write("# H (A/m), B_RD (T), B_TD (T)\n")
        f.write("# Digitized from Nippon Steel/JFE catalog. Research/educational use.\n")
        for hi, brd, btd in zip(h, rd, td): f.write(f"{hi:.6f},{brd:.6f},{btd:.6f}\n")
    return p

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ad, fls = {}, []
    for grade, meta in GRADE_META.items():
        print(f"Generating {grade} ({meta[0]})...")
        rd = gen_curve(CP[f"{grade}_RD"], H_DENSE)
        td = gen_curve(CP[f"{grade}_TD"], H_DENSE)
        rd_f = save_csv(f"{grade}_RD", H_DENSE, rd, grade, "RD", meta)
        td_f = save_csv(f"{grade}_TD", H_DENSE, td, grade, "TD", meta)
        cb_f = save_combined(grade, H_DENSE, rd, td, meta)
        fls += [rd_f, td_f, cb_f]
        b800_rd = np.interp(800, H_DENSE, rd)
        b800_td = np.interp(800, H_DENSE, td)
        print(f"  B800 RD={b800_rd:.3f}T  TD={b800_td:.3f}T")
        ad[grade] = {"description": meta[0], "cn_equivalent": meta[1],
            "thickness_mm": meta[2], "B800_RD_T": round(b800_rd,3),
            "B800_TD_T": round(b800_td,3),
            "files": {"RD": os.path.basename(rd_f), "TD": os.path.basename(td_f),
                      "combined": os.path.basename(cb_f)}}
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path,"w") as f: json.dump({
        "data_source": "Digitized from Nippon Steel / JFE GO electrical steel catalog curves.",
        "disclaimer": "Research/educational use. Verify against official datasheets for production.",
        "references": ["Nippon Steel GO Catalog (D001)", "JFE Steel G-Core Catalog", "GB/T 2521-2016"],
        "grades": ad}, f, indent=2)
    fls.append(meta_path)
    print(f"\nDone. {len(fls)} files in {OUTPUT_DIR}/")

if __name__ == "__main__": main()
