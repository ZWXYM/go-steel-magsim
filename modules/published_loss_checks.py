"""Serve the verified same-grade checks, separate from the material input bank."""
import json
from pathlib import Path

from modules.system_data import csv_bytes, sha256


SOURCE = 'go_steel_data/output/published_go_loss_checks_v1.json'
SOURCE_SHA256 = 'b861aa5c60d6b6cea629366a80f14975ab5a44ee2e27430f1c31381f0c6bb150'


def read_published_loss_checks(project):
    root = Path(project).resolve()
    path = (root / SOURCE).resolve()
    try:
        if not path.is_relative_to(root):
            raise ValueError('出版损耗整理数据必须位于程序目录')
        raw = path.read_bytes()
        if sha256(raw) != SOURCE_SHA256:
            raise ValueError('出版损耗整理数据与核验版本不同')
        return raw, json.loads(raw)
    except OSError as error:
        raise ValueError('出版损耗整理数据不可读取') from error


def figure_frequency_csv(checks):
    return csv_bytes([
        dict(frequency_Hz=row['frequency_Hz'], peak_B_T=row['Bm_T'],
             specific_energy_loss_J_per_kg=row['measured_figure_mJ_kg']/1000,
             direction='unspecified', table_variant='Zhao and Hu 2020 Fig. 9 measured traces, plot readings',
             configuration='Epstein frame, sinusoidal B; figure reading')
        for row in checks['B30P105']['aip']['graph_points']
    ])
