"""
Fe-Si 多晶微磁学仿真集成平台 - Flask Backend v2
"""
import sys, os, threading, uuid, json, io, traceback, re, shutil, subprocess, time, zipfile as zf_mod2, contextlib
import locale
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file, Response, stream_with_context

import matplotlib
matplotlib.use('Agg')

# Pre-load sklearn submodules at startup so concurrent lazy imports never race on a
# partially-initialized sklearn.base (Flask threaded=True can trigger this).
try:
    import sklearn.base, sklearn.multioutput, sklearn.decomposition
    import sklearn.ensemble, sklearn.metrics, sklearn.model_selection, sklearn.preprocessing
except ImportError:
    pass

app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 确保运行时数据目录存在（新机器克隆后自动创建）
for _d in ['data/exports', 'data/datasets', 'data/models', 'input', 'output', 'preinput']:
    Path(os.path.join(SCRIPT_DIR, _d)).mkdir(parents=True, exist_ok=True)
MODULES_DIR = os.path.join(SCRIPT_DIR, 'modules')
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, MODULES_DIR)  # 所有子模块统一放 modules/

import mx3_generator as gis
import batch_scheduler as gpb
import bh_extractor as see_module

import importlib.util
_texture_module = None
def get_texture_module():
    global _texture_module
    if _texture_module is None:
        spec = importlib.util.spec_from_file_location("texture_gen", os.path.join(MODULES_DIR, "odf_texture.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _texture_module = mod
    return _texture_module

# ── Task Management ──────────────────────────────────────────
tasks = {}
running_jobs = {}  # job_id -> {script,lines,done,process,status,started,finished,total}

def create_task(name):
    tid = str(uuid.uuid4())[:10]
    tasks[tid] = {'id': tid, 'name': name, 'status': 'running', 'output': '',
                  'error': None, 'result': None,
                  'started': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'finished': None}
    return tid

def run_task(tid, func, *args, **kwargs):
    def worker():
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            result = func(*args, **kwargs)
            sys.stdout = old
            tasks[tid].update({'status': 'completed', 'output': buf.getvalue(), 'result': result,
                               'finished': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        except Exception as e:
            sys.stdout = old
            tasks[tid].update({'status': 'failed', 'output': buf.getvalue(), 'error': str(e),
                               'traceback': traceback.format_exc(),
                               'finished': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    threading.Thread(target=worker, daemon=True).start()

# ── Frontend ────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── Workspace ──────────────────────────────────────────────
@app.route('/api/workspace', methods=['GET'])
def get_workspace():
    return jsonify({'workspace': os.getcwd()})

@app.route('/api/workspace', methods=['POST'])
def set_workspace():
    d = request.json; p = d.get('path', '').strip()
    if p and os.path.isdir(p):
        os.chdir(p); return jsonify({'success': True, 'workspace': os.getcwd()})
    return jsonify({'error': '路径无效或不存在'}), 400

# ── Texture: Generate Single ────────────────────────────────
@app.route('/api/texture/generate-single', methods=['POST'])
def generate_single_texture():
    data = request.json
    tid = create_task('单一织构生成')
    def do():
        mod = get_texture_module(); mod.configure_fonts()
        r = mod.generate_texture_with_odf(
            f_Goss=float(data.get('f_Goss', 0.7)),
            theta_0=float(data.get('theta_0', 15.0)),
            N_grains=int(data.get('N_grains', 1000)),
            halfwidth=float(data.get('halfwidth', 10.0)),
            sampling_method=data.get('sampling_method', 'importance'),
            plot_odf=bool(data.get('plot_odf', False)),
            output_dir='preinput'
        )
        import numpy as np
        return {'shape': list(r.shape) if r is not None else None}
    run_task(tid, do)
    return jsonify({'task_id': tid})

# ── Texture: Generate Batch ─────────────────────────────────
@app.route('/api/texture/generate-batch', methods=['POST'])
def generate_batch_texture():
    data = request.json
    tid = create_task('批量织构生成')
    def do():
        mod = get_texture_module(); mod.configure_fonts()
        r = mod.batch_generate_textures(
            n_samples=int(data.get('n_samples', 10)),
            f_Goss_range=(float(data.get('f_Goss_min', 0.4)), float(data.get('f_Goss_max', 0.9))),
            theta_0_range=(float(data.get('theta_0_min', 0)), float(data.get('theta_0_max', 45))),
            halfwidth_range=(float(data.get('halfwidth_min', 8)), float(data.get('halfwidth_max', 15))),
            N_grains=int(data.get('N_grains', 1000)),
            sampling_method=data.get('sampling_method', 'lhs'),
            output_dir='preinput',
            save_plots=bool(data.get('save_plots', False))
        )
        return {'count': len(r) if r else 0}
    run_task(tid, do)
    return jsonify({'task_id': tid})

# ── Texture: PreInput listing ───────────────────────────────
@app.route('/api/texture/preinput', methods=['GET'])
def list_preinput():
    preinput = Path('preinput')
    if not preinput.exists():
        return jsonify({'items': []})
    items = []
    pattern = 'grain_orientations_ODF_*.txt'
    for fp in sorted(preinput.glob(pattern)):
        try:
            info = gis.parse_filename(fp.name)
            info['type'] = 'single'; info['full_path'] = str(fp)
            items.append(info)
        except Exception:
            items.append({'type': 'single', 'filename': fp.name, 'full_path': str(fp)})
    for subdir in sorted(preinput.iterdir()):
        if not subdir.is_dir(): continue
        batch_files = sorted(subdir.glob(pattern))
        if not batch_files: continue
        fis = []
        for bf in batch_files:
            try:
                i = gis.parse_filename(bf.name); i['full_path'] = str(bf); fis.append(i)
            except Exception:
                fis.append({'filename': bf.name, 'full_path': str(bf)})
        items.append({'type': 'batch', 'folder_name': subdir.name,
                      'folder_path': str(subdir), 'n_total': len(fis), 'files': fis})
    return jsonify({'items': items})

# ── Texture: Samples listing ────────────────────────────────
@app.route('/api/texture/samples', methods=['GET'])
def list_samples():
    result = {}
    for folder_label, folder_str in [('preinput', 'preinput'), ('input', 'input')]:
        folder = Path(folder_str)
        if not folder.exists():
            result[folder_label] = []; continue
        items = []
        pattern = 'grain_orientations_ODF_*.txt'
        for fp in sorted(folder.glob(pattern)):
            try:
                info = gis.parse_filename(fp.name)
                info['type'] = 'single'; info['full_path'] = str(fp); items.append(info)
            except Exception:
                items.append({'type': 'single', 'filename': fp.name, 'full_path': str(fp)})
        for subdir in sorted(folder.iterdir()):
            if not subdir.is_dir(): continue
            bfs = sorted(subdir.glob(pattern))
            if not bfs: continue
            fis = []
            for f in bfs:
                try:
                    i = gis.parse_filename(f.name); i['full_path'] = str(f); fis.append(i)
                except Exception:
                    fis.append({'filename': f.name, 'full_path': str(f)})
            items.append({'type': 'batch', 'folder_name': subdir.name,
                          'folder_path': str(subdir), 'n_total': len(fis), 'files': fis})
        result[folder_label] = items
    return jsonify(result)

# ── Texture: Move to input ──────────────────────────────────
@app.route('/api/texture/move-to-input', methods=['POST'])
def move_to_input():
    data = request.json
    files = data.get('files', [])
    folders = data.get('folders', [])
    input_dir = Path('input'); input_dir.mkdir(exist_ok=True)
    moved = []
    for fp in files:
        src = Path(fp)
        if src.exists():
            dst = input_dir / src.name
            shutil.copy2(str(src), str(dst)); moved.append(str(dst))
    for folder_path in folders:
        src = Path(folder_path)
        if src.is_dir():
            dst = input_dir / src.name
            if dst.exists(): shutil.rmtree(str(dst))
            shutil.copytree(str(src), str(dst)); moved.append(str(dst))
    return jsonify({'moved': moved, 'count': len(moved)})

# ── MX3: Input items (singles + batch folders) ──────────────
@app.route('/api/mx3/input-files', methods=['GET'])
def list_input_files():
    try:
        items = gis.scan_input_files()
        def clean(o):
            if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
            if isinstance(o, list): return [clean(v) for v in o]
            if hasattr(o, '__fspath__'): return str(o)
            return o
        return jsonify({'items': clean(items)})
    except FileNotFoundError as e:
        return jsonify({'items': [], 'message': str(e)})
    except Exception as e:
        return jsonify({'items': [], 'error': str(e)})

@app.route('/api/mx3/defaults', methods=['GET'])
def get_mx3_defaults():
    cfg = gis.SimulationConfig
    return jsonify({
        'grid_x': cfg.GRID_SIZE_X, 'grid_y': cfg.GRID_SIZE_Y, 'grid_z': cfg.GRID_SIZE_Z,
        'cell_size_nm': cfg.CELL_SIZE * 1e9, 'si_content': cfg.SI_CONTENT,
        'msat': cfg.MSAT, 'aex': cfg.AEX, 'alpha': cfg.ALPHA, 'ku1_base': cfg.KU1_BASE,
        'h_max': cfg.H_MAX, 'n_steps': cfg.N_STEPS,
        'default_angles': cfg.DEFAULT_ANGLES, 'simulation_type': cfg.SIMULATION_TYPE
    })

@app.route('/api/mx3/generate', methods=['POST'])
def generate_mx3():
    data = request.json
    tid = create_task('MX3脚本生成')
    def do():
        cfg = gis.SimulationConfig
        for key, attr, cast in [
            ('grid_x','GRID_SIZE_X',int), ('grid_y','GRID_SIZE_Y',int), ('grid_z','GRID_SIZE_Z',int),
            ('si_content','SI_CONTENT',float), ('msat','MSAT',float), ('aex','AEX',float),
            ('alpha','ALPHA',float), ('ku1_base','KU1_BASE',float), ('h_max','H_MAX',float),
            ('n_steps','N_STEPS',int)
        ]:
            if key in data: setattr(cfg, attr, cast(data[key]))
        if 'cell_size_nm' in data: cfg.CELL_SIZE = float(data['cell_size_nm']) * 1e-9

        # FIX: filter None values before float conversion
        raw_angles = data.get('angles') or []
        if not raw_angles:
            raw_angles = list(cfg.DEFAULT_ANGLES)
        angles = sorted([float(a) for a in raw_angles if a is not None])
        if not angles:
            angles = list(cfg.DEFAULT_ANGLES)

        mode = data.get('mode', 'single')
        selected_files = data.get('selected_files', [])
        generated = []
        for config_info in selected_files:
            orientations = gis.read_orientations(config_info['full_path'])
            config_info['n_grains'] = len(orientations)
            if mode == 'single':
                gis.generate_single_mode(config_info, angles, orientations)
            else:
                gis.generate_complex_mode(config_info, angles, orientations)
            generated.append(config_info.get('short_name', config_info.get('filename', '')))
        return {'generated': generated, 'mode': mode, 'angles': angles}
    run_task(tid, do)
    return jsonify({'task_id': tid})

# ── Batch Scripts ────────────────────────────────────────────
@app.route('/api/batch/scan', methods=['GET'])
def scan_grain_scripts():
    try:
        configs = gpb.scan_grain_scripts_dir()
        result = {}
        for name, cd in configs.items():
            result[name] = {
                'n_grains': cd['n_grains'],
                'angles': {k: {'angle_value': v['angle_value'], 'count': v['count']}
                           for k, v in cd['angles'].items()}
            }
        return jsonify({'configs': result})
    except Exception as e:
        return jsonify({'configs': {}, 'error': str(e)})

@app.route('/api/batch/generate', methods=['POST'])
def generate_batch_scripts():
    data = request.json
    try:
        configs = gpb.scan_grain_scripts_dir()
        selected_configs = []
        for item in data.get('selected', []):
            name = item['config_name']; codes = item['angle_codes']
            if name in configs:
                selected_configs.append((name, configs[name], codes))
        if not selected_configs:
            return jsonify({'error': '未找到有效配置'}), 400
        ts = gpb.get_timestamp()
        if len(selected_configs) == 1:
            angle_str = gpb.format_angle_list(selected_configs[0][2])
            base_name = f"run_{selected_configs[0][0]}_angles_{angle_str}"
        else:
            base_name = f"run_multi_configs_{ts}"
        script_type = data.get('script_type', '5')
        mumax3_path = data.get('mumax3_path', None)
        # 确保 scripts/ 目录存在
        Path('scripts').mkdir(exist_ok=True)
        generated_files = []
        if script_type in ['1','5']:
            f = f"scripts/{base_name}.bat"; gpb.generate_multi_config_batch_script(selected_configs, f); generated_files.append(f)
        if script_type in ['2','5']:
            f = f"scripts/{base_name}.ps1"; gpb.generate_multi_config_powershell_script(selected_configs, f); generated_files.append(f)
        if script_type in ['3','5']:
            f = f"scripts/{base_name}.sh"; gpb.generate_multi_config_bash_script(selected_configs, f); generated_files.append(f)
        if script_type == '4' and mumax3_path:
            f = f"scripts/{base_name}_custom.sh"; gpb.generate_multi_config_bash_script(selected_configs, f, mumax3_path); generated_files.append(f)
        total_tasks = sum(d['n_grains'] * len(c) for _, d, c in selected_configs)
        return jsonify({'success': True, 'files': generated_files, 'total_tasks': total_tasks})
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

# ── Task Scripts Management ──────────────────────────────────
@app.route('/api/task-scripts', methods=['GET'])
def list_task_scripts():
    scripts = []
    # 扫描 scripts/ 子目录（新位置）和根目录（兼容旧文件）
    scan_dirs = [Path('scripts'), Path('.')]
    seen = set()
    for scan_dir in scan_dirs:
        if not scan_dir.exists(): continue
        for ext in ['*.bat','*.ps1','*.sh']:
            for f in scan_dir.glob(ext):
                if not f.name.startswith('run_'): continue
                if f.name in seen: continue  # 根目录同名文件已被 scripts/ 覆盖
                seen.add(f.name)
                stat = f.stat(); task_count = 0; configs = []; angles_info = []
                try:
                    content = f.read_text(encoding='utf-8', errors='ignore')
                    bat_m = re.findall(r'for /L %%G in \(1,1,(\d+)\)', content)
                    ps1_m = re.findall(r'\$g -le (\d+)', content)
                    sh_m  = re.findall(r'seq 1 (\d+)', content)
                    if bat_m:  task_count = sum(int(x) for x in bat_m)
                    elif ps1_m: task_count = sum(int(x) for x in ps1_m)
                    elif sh_m:  task_count = sum(int(x) for x in sh_m)
                    configs = list(set(re.findall(r'Configuration \d+/\d+: (\S+)', content)))
                    angles_info = sorted(set(re.findall(r'Angle (\d+) degrees', content)))
                except Exception: pass
                scripts.append({'name': f.name, 'size': stat.st_size,
                                'created': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                                'type': f.suffix[1:], 'task_count': task_count,
                                'configs': configs, 'angles': angles_info,
                                'path': str(f).replace('\\', '/')})
    return jsonify({'scripts': sorted(scripts, key=lambda x: x['created'], reverse=True)})

@app.route('/api/task-scripts/<path:filename>', methods=['DELETE'])
def delete_task_script(filename):
    import shutil as _shutil
    cascade = request.args.get('cascade', 'true').lower() != 'false'
    fname = filename.replace('\\', '/')
    candidates = [
        Path(fname),
        Path('scripts') / Path(fname).name,
        Path(fname.split('/')[-1]),
    ]
    target = None
    for p in candidates:
        if p.exists() and p.suffix in ['.bat', '.ps1', '.sh']:
            target = p; break
    if not target:
        return jsonify({'error': '文件不存在'}), 404

    deleted = []
    if cascade:
        stem = target.stem  # e.g. run_pipeline_abc  or  run_ml_dataset_xyz
        if stem.startswith('run_'):
            run_id = stem[4:]          # strip leading 'run_'
            # 删除同批次其他格式脚本（.bat / .ps1 / .sh）
            for ext in ['.bat', '.ps1', '.sh']:
                sib = target.parent / (stem + ext)
                if sib.exists() and sib != target:
                    sib.unlink(); deleted.append(_posix(sib))
            # 删除 manifest JSON
            for mname in [f'{stem}_manifest.json', f'{run_id}_manifest.json']:
                mp = target.parent / mname
                if mp.exists():
                    mp.unlink(); deleted.append(_posix(mp)); break
            # 删除关联的 preinput/ 和 grain_scripts/ 子目录
            for base in ['preinput', 'grain_scripts']:
                d = Path(base) / run_id
                if d.exists() and d.is_dir():
                    _shutil.rmtree(d); deleted.append(_posix(d) + '/')

    target.unlink(); deleted.insert(0, _posix(target))
    return jsonify({'success': True, 'deleted': deleted})


@app.route('/api/grain-scripts/<path:config_name>', methods=['DELETE'])
def delete_grain_script_config(config_name):
    import shutil as _shutil
    d = Path('grain_scripts') / config_name.replace('\\', '/').lstrip('/')
    if not d.exists() or not d.is_dir():
        return jsonify({'error': '配置目录不存在'}), 404
    _shutil.rmtree(d)
    return jsonify({'success': True, 'deleted': _posix(d) + '/'})


@app.route('/api/preinput-item', methods=['DELETE'])
def delete_preinput_item():
    import shutil as _shutil
    data = request.json or {}
    raw = data.get('path', '').replace('\\', '/')
    item_type = data.get('type', 'auto')   # 'file' | 'folder' | 'auto'
    p = Path(raw)
    # 安全检查：路径必须在 preinput/ 下
    try:
        p.resolve().relative_to(Path('preinput').resolve())
    except ValueError:
        return jsonify({'error': '路径不在 preinput/ 目录内'}), 403
    if not p.exists():
        return jsonify({'error': '路径不存在'}), 404
    if p.is_dir():
        _shutil.rmtree(p)
    else:
        p.unlink()
    return jsonify({'success': True, 'deleted': _posix(p)})


@app.route('/api/input-item', methods=['DELETE'])
def delete_input_item():
    import shutil as _shutil
    data = request.json or {}
    raw = data.get('path', '').replace('\\', '/')
    p = Path(raw)
    try:
        p.resolve().relative_to(Path('input').resolve())
    except ValueError:
        return jsonify({'error': '路径不在 input/ 目录内'}), 403
    if not p.exists():
        return jsonify({'error': '路径不存在'}), 404
    if p.is_dir():
        _shutil.rmtree(p)
    else:
        p.unlink()
    return jsonify({'success': True, 'deleted': _posix(p)})


@app.route('/api/output-item', methods=['DELETE'])
def delete_output_item():
    import shutil as _shutil
    data = request.json or {}
    raw = data.get('path', '').replace('\\', '/')
    p = Path(raw)
    try:
        p.resolve().relative_to(Path('output').resolve())
    except ValueError:
        return jsonify({'error': '路径不在 output/ 目录内'}), 403
    if not p.exists():
        return jsonify({'error': '路径不存在'}), 404
    if p.is_dir():
        _shutil.rmtree(p)
    else:
        p.unlink()
    return jsonify({'success': True, 'deleted': _posix(p)})


@app.route('/api/task-scripts/<path:filename>/preview', methods=['GET'])
def preview_task_script(filename):
    p = Path(filename)
    if p.exists():
        try: return jsonify({'content': p.read_text(encoding='utf-8', errors='replace')[:8000]})
        except Exception as e: return jsonify({'error': str(e)}), 500
    return jsonify({'error': '文件不存在'}), 404

@app.route('/api/download/<path:filename>')
def download_file(filename):
    p = Path(filename)
    if p.exists(): return send_file(str(p.resolve()), as_attachment=True)
    return jsonify({'error': '文件不存在'}), 404

# ── Analysis: Upload ─────────────────────────────────────────
@app.route('/api/analyze', methods=['POST'])
def analyze():
    if 'file' not in request.files:
        return jsonify({'error': '请上传文件'}), 400
    f = request.files['file']
    Msat = float(request.form.get('Msat', 1.56e6))
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.txt', prefix='mumax_')
    os.close(fd)
    try:
        f.save(tmp)
        return _do_analyze(tmp, Msat)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500
    finally:
        try:
            if os.path.exists(tmp): os.unlink(tmp)
        except Exception:
            pass

def _do_analyze(filepath, Msat):
    import numpy as np
    data = see_module.read_mumax_data(filepath)
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    results, H, M, B, M_mag, E_total = see_module.extract_magnetic_properties(data, Msat=Msat)
    sys.stdout = old
    def sub(arr, n=300):
        arr = np.array(arr)
        if len(arr) > n:
            idx = np.linspace(0, len(arr)-1, n, dtype=int); return arr[idx].tolist()
        return arr.tolist()
    props = {k: float(results[k]) for k in ['Ms','Mr','Hc','mu_r_max_total','mu_r_max_diff',
                                              'hysteresis_loss','Mr_Ms_ratio','M_mag_min','M_mag_mean','Msat']}
    curves = {
        'H_full': sub(H), 'M_full': sub(M), 'B_full': sub(results['B_full']),
        'H_curve': sub(results['H_curve']), 'M_curve': sub(results['M_curve']),
        'B_curve': sub(results['B_curve']),
        'mu_r_total': sub(results['mu_r_total']), 'mu_r_diff': sub(results['mu_r_diff']),
        'M_mag': sub(M_mag), 'E_total': sub(E_total)
    }
    return jsonify({'properties': props, 'curves': curves, 'data_points': len(data), 'log': buf.getvalue()})

# ── Analysis: Scan Output Folder ─────────────────────────────
@app.route('/api/analysis/scan-output', methods=['GET'])
def scan_output():
    output_base = Path('output')
    if not output_base.exists():
        return jsonify({'runs': [], 'error': 'output/ 目录不存在'})
    runs = []
    for run_dir in sorted(output_base.iterdir(), reverse=True):
        if not run_dir.is_dir() or not run_dir.name.startswith('run_'): continue
        run_info = {'name': run_dir.name, 'path': str(run_dir), 'configs': []}
        _scan_run_dir(run_dir, run_info)
        if run_info['configs']:
            runs.append(run_info)
    return jsonify({'runs': runs})

def _posix(p):
    """Normalize path to forward slashes for safe JSON/HTML embedding."""
    return str(p).replace('\\', '/')

def _scan_run_dir(run_dir, run_info):
    angle_dirs = [d for d in run_dir.iterdir() if d.is_dir() and d.name.startswith('angle_')]
    if angle_dirs:
        config_info = {'name': run_dir.name, 'path': _posix(run_dir), 'angles': []}
        for adir in sorted(angle_dirs):
            m = re.match(r'angle_(\d+)', adir.name)
            if not m: continue
            grain_files = sorted(adir.glob('grain_*.txt'))
            if grain_files:
                config_info['angles'].append({
                    'code': m.group(1), 'angle_value': int(m.group(1)),
                    'path': _posix(adir), 'file_count': len(grain_files),
                    'files': [_posix(f) for f in grain_files]
                })
        if config_info['angles']:
            run_info['configs'].append(config_info)
    else:
        for subdir in sorted(run_dir.iterdir()):
            if not subdir.is_dir(): continue
            angle_dirs2 = [d for d in subdir.iterdir() if d.is_dir() and d.name.startswith('angle_')]
            if not angle_dirs2: continue
            config_info = {'name': subdir.name, 'path': _posix(subdir), 'angles': []}
            for adir in sorted(angle_dirs2):
                m = re.match(r'angle_(\d+)', adir.name)
                if not m: continue
                grain_files = sorted(adir.glob('grain_*.txt'))
                if grain_files:
                    config_info['angles'].append({
                        'code': m.group(1), 'angle_value': int(m.group(1)),
                        'path': _posix(adir), 'file_count': len(grain_files),
                        'files': [_posix(f) for f in grain_files]
                    })
            if config_info['angles']:
                run_info['configs'].append(config_info)

@app.route('/api/analysis/analyze-path', methods=['POST'])
def analyze_path():
    data = request.json
    raw_path = data.get('path', '').strip()
    Msat = float(data.get('Msat', 1.56e6))
    # Paths are sent with forward slashes; normalize to OS-native separators
    filepath = os.path.normpath(raw_path)
    # Also try the raw path in case running on Linux where / is native
    candidates = [filepath, raw_path]
    resolved = None
    for c in candidates:
        if c and os.path.exists(c):
            resolved = c
            break
    if not resolved:
        return jsonify({'error': f'文件不存在: {raw_path}  (also tried: {filepath})'}), 400
    try:
        return _do_analyze(resolved, Msat)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/analysis/analyze-angle', methods=['POST'])
def analyze_angle_avg():
    """对一个角度目录下的所有晶粒文件做 B-H 曲线平均，返回统计结果。"""
    import numpy as np
    data = request.get_json(silent=True) or {}
    if not data and request.data:
        try:
            data = json.loads(request.data.decode('utf-8'))
        except Exception:
            data = {}
    filepaths = data.get('filepaths', [])
    Msat = float(data.get('Msat', 1.56e6))
    if not filepaths:
        return jsonify({'error': '未提供文件列表'}), 400

    all_results = []; errors = []
    for raw_fp in filepaths:
        fp = os.path.normpath(raw_fp)
        if not os.path.exists(fp): fp = raw_fp
        if not os.path.exists(fp):
            errors.append(f'文件不存在: {os.path.basename(raw_fp)}'); continue
        try:
            d = see_module.read_mumax_data(fp)
            _stdout_save = sys.stdout; sys.stdout = io.StringIO()
            r2, H, M, B, M_mag, E_total = see_module.extract_magnetic_properties(d, Msat=Msat)
            sys.stdout = _stdout_save
            if len(r2.get('H_curve', [])) > 2:
                all_results.append(r2)
        except Exception as e:
            sys.stdout = _stdout_save if '_stdout_save' in dir() else sys.stdout
            errors.append(f'{os.path.basename(raw_fp)}: {e}')

    if not all_results:
        return jsonify({'error': '无有效分析结果', 'errors': errors[:10]}), 500

    ref = all_results[len(all_results) // 2]
    H_ref = np.array(ref['H_curve'])
    scalar_keys = ['Ms','Mr','Hc','mu_r_max_total','mu_r_max_diff','hysteresis_loss','Mr_Ms_ratio']
    scalars = {k: [] for k in scalar_keys}
    B_mat = []; M_mat = []

    for r2 in all_results:
        H_i = np.array(r2['H_curve']); B_i = np.array(r2['B_curve']); M_i = np.array(r2['M_curve'])
        if len(H_i) == len(H_ref):
            B_mat.append(B_i); M_mat.append(M_i)
        else:
            B_mat.append(np.interp(H_ref, H_i, B_i))
            M_mat.append(np.interp(H_ref, H_i, M_i))
        for k in scalar_keys:
            if k in r2: scalars[k].append(float(r2[k]))

    B_arr = np.array(B_mat); M_arr = np.array(M_mat)
    B_avg = B_arr.mean(axis=0); B_std = B_arr.std(axis=0); M_avg = M_arr.mean(axis=0)

    mu0 = 4e-7 * np.pi
    H_safe = np.where(np.abs(H_ref) > 1.0, H_ref, np.sign(H_ref + 1e-15))
    mu_r_tot = B_avg / (mu0 * H_safe)
    mu_r_dif = np.gradient(B_avg, H_ref) / mu0

    avg_s = {k: float(np.mean(v)) if v else 0.0 for k, v in scalars.items()}
    avg_s.update({'Msat': Msat, 'M_mag_min': 1.0, 'M_mag_mean': 1.0})

    def sub(arr, n=300):
        arr = np.array(arr)
        if len(arr) > n:
            idx = np.linspace(0, len(arr)-1, n, dtype=int); return arr[idx].tolist()
        return arr.tolist()

    return jsonify({
        'properties': avg_s,
        'curves': {
            'H_curve': sub(H_ref), 'B_curve': sub(B_avg), 'M_curve': sub(M_avg),
            'B_std':   sub(B_std),
            'H_full':  sub(H_ref), 'B_full': sub(B_avg), 'M_full': sub(M_avg),
            'mu_r_total': sub(mu_r_tot), 'mu_r_diff': sub(mu_r_dif),
            'M_mag': sub(np.ones_like(H_ref)), 'E_total': sub(np.zeros_like(H_ref)),
        },
        'grain_count': len(all_results), 'error_count': len(errors),
        'errors': errors[:5], 'data_points': len(H_ref), 'has_std': True,
    })


@app.route('/api/analysis/material-representative', methods=['GET'])
def material_representative():
    raw_path = request.args.get('config_path', '').strip()
    Msat = float(request.args.get('Msat', 1.56e6))
    if not raw_path:
        return jsonify({'error': 'config_path is required'}), 400
    config_path = os.path.normpath(raw_path)
    if not os.path.exists(config_path) and os.path.exists(raw_path):
        config_path = raw_path
    if not os.path.isdir(config_path):
        return jsonify({'error': f'配置目录不存在: {raw_path}'}), 404
    try:
        from dataset_builder import DatasetBuilder
        summary = DatasetBuilder().build_material_representative_summary(
            config_path, Msat=Msat, write_report=True
        )
        return jsonify(summary)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/analysis/full-direction', methods=['GET'])
def full_direction_analysis():
    raw_path = request.args.get('config_path', '').strip()
    Msat = float(request.args.get('Msat', 1.56e6))
    if not raw_path:
        return jsonify({'error': 'config_path is required'}), 400
    config_path = os.path.normpath(raw_path)
    if not os.path.exists(config_path) and os.path.exists(raw_path):
        config_path = raw_path
    if not os.path.isdir(config_path):
        return jsonify({'error': f'配置目录不存在: {raw_path}'}), 404
    try:
        import numpy as np
        from dataset_builder import DatasetBuilder
        from anisotropy_interpolator import interpolate_full_direction
        from physics_calibrator import MU0

        summary = DatasetBuilder().build_material_representative_summary(
            config_path, Msat=Msat, write_report=True
        )
        angles = summary.get('angles', {})
        rd = angles.get('0')
        td = angles.get('90')
        if not rd or rd.get('status') != 'ok' or not td or td.get('status') != 'ok':
            return jsonify({'error': '需要 angle_000 与 angle_090 的有效材料级代表曲线'}), 400

        full = interpolate_full_direction(
            {'H': rd['H'], 'B': rd['B'], 'source': 'analysis_RD'},
            {'H': td['H'], 'B': td['B'], 'source': 'analysis_TD'},
        )
        measured_overlay = []
        for angle_key, item in angles.items():
            if item.get('status') != 'ok':
                continue
            angle = int(angle_key)
            if angle in (0, 90):
                continue
            H = item.get('H') or []
            B = item.get('B') or []
            if not H or not B:
                continue
            b800 = float(np.interp(800, H, B))
            measured_overlay.append({
                'angle_deg': angle,
                'B800_T': b800,
                'mu800': b800 / (MU0 * 800.0),
                'curve': {'H': H, 'B': B},
                'source': f'angle_{angle:03d}',
                'n_grains_valid': item.get('n_grains_valid'),
            })

        payload = {
            'config_name': summary.get('config_name'),
            'config_path': summary.get('config_path'),
            'rd_source': 'angle_000',
            'td_source': 'angle_090',
            **full,
            'measured_overlay': measured_overlay,
            'material_representative_sidecar': summary.get('sidecar_path'),
        }
        return jsonify(payload)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/jobs/clear-done', methods=['POST'])
def clear_done_jobs():
    """从 running_jobs 中移除已完成/失败/停止的任务。"""
    done_ids = [jid for jid, job in list(running_jobs.items()) if job.get('done')]
    for jid in done_ids:
        running_jobs.pop(jid, None)
    return jsonify({'cleared': len(done_ids)})


@app.route('/api/analyze/export-femm', methods=['POST'])
def export_femm():
    import numpy as np
    data = request.json
    H = np.array(data['H_curve']); B = np.array(data['B_curve'])
    buf = io.StringIO()
    buf.write("# B-H curve for FEMM\n# H(A/m)\tB(T)\n")
    for h, b in zip(H, B): buf.write(f"{h:.6e}\t{b:.6e}\n")
    out = io.BytesIO(buf.getvalue().encode('utf-8'))
    return send_file(out, mimetype='text/plain', as_attachment=True, download_name='femm_bh_curve.txt')


# ── Task Scripts: Check configs ──────────────────────────────
def _extract_configs_from_script(file_content):
    """Extract unique grain_scripts config names referenced in batch script."""
    matches = re.findall(r'grain_scripts[/\\]+([A-Za-z0-9_]+)[/\\]+', file_content)
    return sorted(set(matches))

@app.route('/api/task-scripts/<path:filename>/check', methods=['GET'])
def check_task_script_configs(filename):
    p = Path(filename)
    if not p.exists():
        return jsonify({'error': f'脚本不存在: {filename}'}), 404
    try:
        content = p.read_text(encoding='utf-8', errors='ignore')
        config_names = _extract_configs_from_script(content)
        gs_dir = Path('grain_scripts')
        configs = []
        all_ok = True
        for name in config_names:
            cdir = gs_dir / name
            exists = cdir.is_dir()
            mx3s = list(cdir.rglob('*.mx3')) if exists else []
            angle_dirs = [d for d in cdir.iterdir()
                          if d.is_dir() and d.name.startswith('angle_')] if exists else []
            ok = exists and len(mx3s) > 0
            if not ok:
                all_ok = False
            configs.append({'name': name, 'exists': exists, 'ok': ok,
                            'mx3_count': len(mx3s), 'angle_count': len(angle_dirs)})
        return jsonify({'configs': configs,
                        'all_ok': all_ok and bool(config_names),
                        'total_mx3': sum(c['mx3_count'] for c in configs)})
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

# ── Task Scripts: ZIP download with MX3 ──────────────────────
@app.route('/api/task-scripts/<path:filename>/download-zip', methods=['GET'])
def download_script_with_mx3(filename):
    import zipfile as zf_mod
    p = Path(filename)
    if not p.exists():
        return jsonify({'error': f'脚本不存在: {filename}'}), 404
    try:
        content = p.read_text(encoding='utf-8', errors='ignore')
        config_names = _extract_configs_from_script(content)
        buf = io.BytesIO()
        with zf_mod.ZipFile(buf, 'w', zf_mod.ZIP_DEFLATED) as zf:
            # Add the batch script itself
            zf.write(str(p), p.name)
            # Add all mx3 + param files for each config
            gs_dir = Path('grain_scripts')
            for name in config_names:
                cdir = gs_dir / name
                if cdir.is_dir():
                    for fp in sorted(cdir.rglob('*')):
                        if fp.is_file():
                            # Preserve relative path from project root
                            try:
                                arcname = fp.relative_to('.').as_posix()
                            except ValueError:
                                arcname = fp.as_posix()
                            zf.write(str(fp), arcname)
        buf.seek(0)
        zip_name = p.stem + '_package.zip'
        return send_file(buf, mimetype='application/zip',
                         as_attachment=True, download_name=zip_name)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

# ── Analysis: Upload results (ZIP or folder) ─────────────────
@app.route('/api/analysis/upload-results', methods=['POST'])
def upload_results():
    import zipfile as zf_mod
    output_base = Path('output')
    output_base.mkdir(exist_ok=True)

    # ── ZIP upload ──
    if 'zipfile' in request.files:
        f = request.files['zipfile']
        try:
            buf = io.BytesIO(f.read())
            with zf_mod.ZipFile(buf) as zf:
                names = zf.namelist()
                zf.extractall(str(output_base))
            top_dirs = sorted(set(n.split('/')[0] for n in names
                                  if n.split('/')[0] and not n.split('/')[0].startswith('.')))
            return jsonify({'success': True,
                            'message': f'已解压 {len(names)} 个文件到 output/',
                            'dirs': top_dirs})
        except zf_mod.BadZipFile:
            return jsonify({'error': '文件不是有效的 ZIP 压缩包'}), 400
        except Exception as e:
            return jsonify({'error': f'解压失败: {str(e)}'}), 500

    # ── Folder upload (webkitdirectory) ──
    if 'files' in request.files:
        files_list = request.files.getlist('files')
        paths_list = request.form.getlist('paths')
        count = 0
        for f, rel_path in zip(files_list, paths_list):
            if rel_path:
                # Normalize separators
                rel_path = rel_path.replace('\\', '/')
                dest = output_base / Path(rel_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    f.save(str(dest))
                    count += 1
                except Exception:
                    pass
        return jsonify({'success': True,
                        'message': f'已上传 {count} 个文件至 output/',
                        'count': count})

    return jsonify({'error': '未收到文件'}), 400


# ── Environment Check ────────────────────────────────────────
@app.route('/api/env-check', methods=['GET'])
def env_check():
    import platform
    result = {
        'platform': platform.system(),
        'platform_detail': f"{platform.system()} {platform.release()}",
        'python': sys.version.split()[0],
        'mumax3': None,
        'cuda': None,
        'cwd': os.getcwd(),
    }
    # Check mumax3
    try:
        r = subprocess.run(['mumax3', '-v'], capture_output=True, timeout=8,
                           text=True, errors='replace')
        out = (r.stdout + r.stderr).strip()[:200]
        result['mumax3'] = {'ok': True, 'output': out or 'mumax3 responded (no version output)'}
    except FileNotFoundError:
        result['mumax3'] = {'ok': False, 'error': 'mumax3 not found in PATH'}
    except subprocess.TimeoutExpired:
        result['mumax3'] = {'ok': True, 'output': 'Timeout — mumax3 is present (start-up took >8s)'}
    except Exception as e:
        result['mumax3'] = {'ok': False, 'error': str(e)}
    # Check CUDA via nvidia-smi
    try:
        r = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total,driver_version',
             '--format=csv,noheader'],
            capture_output=True, timeout=8, text=True, errors='replace')
        if r.returncode == 0:
            gpus = [g.strip() for g in r.stdout.strip().split('\n') if g.strip()]
            result['cuda'] = {'ok': True, 'gpus': gpus}
        else:
            result['cuda'] = {'ok': False, 'error': r.stderr.strip()[:200] or 'nvidia-smi failed'}
    except FileNotFoundError:
        result['cuda'] = {'ok': False, 'error': 'nvidia-smi not found (no NVIDIA driver or not in PATH)'}
    except subprocess.TimeoutExpired:
        result['cuda'] = {'ok': True, 'gpus': ['Timeout — GPU may be present']}
    except Exception as e:
        result['cuda'] = {'ok': False, 'error': str(e)}
    return jsonify(result)

# ── Job Execution ────────────────────────────────────────────
def _build_cmd(script_path, mumax3_custom=None):
    """Build OS-appropriate command to execute a batch script."""
    import platform
    system = platform.system()
    ext = Path(script_path).suffix.lower()
    abs_path = str(Path(script_path).resolve())
    if ext == '.bat':
        return ['cmd.exe', '/c', abs_path]
    elif ext == '.ps1':
        return ['powershell.exe', '-ExecutionPolicy', 'Bypass',
                '-NonInteractive', '-File', abs_path]
    elif ext == '.sh':
        if system == 'Windows':
            return ['bash', abs_path]
        return ['bash', abs_path]
    return None

def _ansi_strip(s):
    return re.sub(r'\033\[[0-9;]*[mKHFA-Za-z]|\x1b\[[0-9;]*[mKHFA-Za-z]', '', s)

# ── Job queue ────────────────────────────────────────────────
job_queue = []          # list of (job_id) waiting to run
job_queue_lock = threading.Lock()

def _parse_script_total(filename):
    try:
        raw = Path(filename).read_bytes()
        for enc in ['utf-8', 'gbk', 'latin-1']:
            try:
                sc = raw.decode(enc); break
            except Exception:
                sc = raw.decode('latin-1', errors='replace')
        bat = re.findall(r'for /L %%G in \(1,1,(\d+)\)', sc)
        ps1 = re.findall(r'\$g -le (\d+)', sc)
        sh  = re.findall(r'seq 1 (\d+)', sc)
        nums = bat or ps1 or sh
        return sum(int(x) for x in nums) if nums else 0
    except Exception:
        return 0

def _make_job(filename, cmd):
    return {
        'id': str(uuid.uuid4())[:12],
        'script': filename,
        'cmd': cmd,
        'lines': [],
        'done': False,
        'process': None,
        'status': 'queued',          # queued | starting | running | completed | failed | stopped
        'started': None,
        'queued_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'finished': None,
        'total': _parse_script_total(filename),
        'completed': 0,
        'failed': 0,
        'lock': threading.Lock(),
    }

def _active_job():
    """Return the currently running (non-done, non-queued) job, if any."""
    for jid, job in running_jobs.items():
        if not job['done'] and job['status'] not in ('queued',):
            return job
    return None

def _start_job(job):
    """Actually launch the subprocess for a job. Call from a thread."""
    job['status'] = 'starting'
    job['started'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    enc = locale.getpreferredencoding(False) or 'utf-8'

    def reader():
        try:
            proc = subprocess.Popen(
                job['cmd'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,   # 防止 BAT pause 命令阻塞
                cwd=os.getcwd(),
                bufsize=1,
            )
            job['process'] = proc
            job['status'] = 'running'
            for raw_line in proc.stdout:
                for e in ['utf-8', enc, 'latin-1']:
                    try:
                        line = raw_line.decode(e).rstrip('\r\n'); break
                    except Exception:
                        pass
                line = _ansi_strip(line)
                pm = re.search(r'Global Progress:.*?(\d+)/(\d+)', line)  # 兼容 BAT "44%  (16/36)" 和 PS1 "16/36" 格式
                if pm:
                    job['completed'] = int(pm.group(1))
                if '[FAIL]' in line or '[ERROR]' in line or 'failed!' in line.lower():
                    job['failed'] += 1
                with job['lock']:
                    job['lines'].append(line)
            proc.wait()
            rc = proc.returncode
            job['status'] = 'completed' if rc == 0 else f'failed(rc={rc})'
        except Exception as e:
            with job['lock']:
                job['lines'].append(f'[RUNNER ERROR] {e}')
            job['status'] = 'error'
        finally:
            job['done'] = True
            job['finished'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # ── Start next queued job ────────────────────────
            _advance_queue()

    threading.Thread(target=reader, daemon=True).start()

def _advance_queue():
    """Start the next job in queue, if any."""
    with job_queue_lock:
        if not job_queue:
            return
        next_id = job_queue.pop(0)
    job = running_jobs.get(next_id)
    if job:
        _start_job(job)

@app.route('/api/run-script', methods=['POST'])
def run_script():
    data = request.json
    filename = data.get('filename', '')
    p = Path(filename)
    if not p.exists():
        return jsonify({'error': f'脚本不存在: {filename}'}), 404
    cmd = _build_cmd(str(p))
    if not cmd:
        return jsonify({'error': '不支持的脚本类型'}), 400

    job = _make_job(filename, cmd)
    running_jobs[job['id']] = job

    active = _active_job()
    if active:
        # Queue it
        with job_queue_lock:
            job_queue.append(job['id'])
        return jsonify({'job_id': job['id'], 'total': job['total'],
                        'queued': True, 'queue_pos': len(job_queue)})
    else:
        _start_job(job)
        return jsonify({'job_id': job['id'], 'total': job['total'], 'queued': False})

@app.route('/api/jobs/<job_id>/stream')
def stream_job(job_id):
    if job_id not in running_jobs:
        return jsonify({'error': 'Job not found'}), 404
    def generate():
        job = running_jobs[job_id]
        offset = 0
        while True:
            with job['lock']:
                cur_len = len(job['lines'])
            while offset < cur_len:
                with job['lock']:
                    line = job['lines'][offset]
                payload = json.dumps({
                    'line': line,
                    'completed': job['completed'],
                    'total': job['total'],
                    'failed': job['failed'],
                    'status': job['status'],
                })
                yield f"data: {payload}\n\n"
                offset += 1
            if job['done']:
                yield f"data: {json.dumps({'done': True, 'status': job['status'], 'completed': job['completed'], 'total': job['total'], 'failed': job['failed']})}\n\n"
                break
            time.sleep(0.15)
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                 'Access-Control-Allow-Origin': '*'}
    )

@app.route('/api/jobs/<job_id>/status')
def job_status(job_id):
    if job_id not in running_jobs:
        return jsonify({'error': 'Not found'}), 404
    job = running_jobs[job_id]
    with job['lock']:
        recent = job['lines'][-50:]
    return jsonify({
        'id': job_id, 'script': job['script'],
        'status': job['status'], 'done': job['done'],
        'total': job['total'], 'completed': job['completed'],
        'failed': job['failed'],
        'started': job['started'], 'finished': job['finished'],
        'recent_lines': recent,
    })

@app.route('/api/jobs/<job_id>/stop', methods=['POST'])
def stop_job(job_id):
    if job_id not in running_jobs:
        return jsonify({'error': 'Not found'}), 404
    job = running_jobs[job_id]
    proc = job.get('process')
    if proc and proc.poll() is None:
        try:
            import signal
            if os.name == 'nt':
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            job['status'] = 'stopped'
            job['done'] = True
            job['finished'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return jsonify({'success': True, 'message': '任务已停止'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'message': '任务已经结束或未启动'})

@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    result = []
    for jid, job in running_jobs.items():
        result.append({
            'id': jid, 'script': job['script'],
            'status': job['status'], 'done': job['done'],
            'total': job['total'], 'completed': job['completed'],
            'failed': job['failed'],
            'started': job['started'] or job.get('queued_at'),
            'finished': job['finished'],
            'queued_at': job.get('queued_at'),
        })
    # Sort: running first, then queued, then by time desc
    def sort_key(j):
        order = {'running':0,'starting':1,'queued':2,'completed':3,'failed':4,'stopped':4,'error':4}
        return (order.get(j['status'],5), -(0 if not j['started'] else 0))
    return jsonify({'jobs': sorted(result, key=lambda x: (0 if x['status']=='running' else 1 if x['status']=='queued' else 2, x['started'] or ''), reverse=False),
                    'queue': list(job_queue)})

@app.route('/api/jobs/queue', methods=['GET'])
def get_queue():
    with job_queue_lock:
        q = list(job_queue)
    return jsonify({'queue': q, 'length': len(q)})

# ── Task Status ──────────────────────────────────────────────
@app.route('/api/tasks/<tid>', methods=['GET'])
def get_task_status(tid):
    if tid not in tasks: return jsonify({'error': 'Not found'}), 404
    t = tasks[tid].copy()
    if isinstance(t.get('result'), dict):
        t['result'] = {k: v for k, v in t['result'].items() if not hasattr(v, 'tolist')}
    return jsonify(t)

@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    return jsonify({'tasks': [{k: v for k, v in t.items() if k not in ('result','traceback')}
                               for t in tasks.values()]})



# ════════════════════════════════════════════════════════════════════
# 新增模块（懒加载，启动失败不影响已有功能）
# ════════════════════════════════════════════════════════════════════
_dataset_builder = None
_ml_predictor    = None
_maxwell_exp     = None
_pipeline_runner = None

def _get_db():
    global _dataset_builder
    if _dataset_builder is None:
        from dataset_builder import DatasetBuilder
        _dataset_builder = DatasetBuilder()
    return _dataset_builder

def _get_ml():
    global _ml_predictor
    if _ml_predictor is None:
        from ml_trainer import BHPredictor
        _ml_predictor = BHPredictor()
    return _ml_predictor

def _get_mx():
    global _maxwell_exp
    if _maxwell_exp is None:
        import maxwell_exporter
        _maxwell_exp = maxwell_exporter
    return _maxwell_exp

def _get_pr():
    global _pipeline_runner
    if _pipeline_runner is None:
        from pipeline_runner import PipelineRunner
        _pipeline_runner = PipelineRunner()
    return _pipeline_runner


# -- dataset management ---------------------------------------------------

@app.route('/api/dataset/scan', methods=['GET'])
def dataset_scan():
    try:
        return jsonify({'configs': _get_db().scan_output_dir()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dataset/list', methods=['GET'])
def dataset_list():
    try:
        return jsonify({'datasets': _get_db().list_datasets()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dataset/aggregate', methods=['POST'])
def dataset_aggregate():
    data    = request.json or {}
    configs = data.get('configs')
    config_paths = data.get('config_paths')
    mode    = data.get('angle_mode', 'motor')
    Msat    = float(data.get('Msat', 1.56e6))
    from dataset_builder import MOTOR_ANGLES, FULL_ANGLES
    target_angles = MOTOR_ANGLES if mode == 'motor' else FULL_ANGLES
    tag     = data.get('tag', mode)
    tid     = create_task('dataset_aggregate')

    def do_aggregate():
        db = _get_db()
        log = []
        def cb(cur, tot, name):
            log.append('(%d/%d) %s' % (cur, tot, name))
        df   = db.build_dataset(configs=configs, config_paths=config_paths,
                                target_angles=target_angles, Msat=Msat,
                                progress_callback=cb)
        if len(df) == 0:
            raise ValueError('no valid selected simulation results were aggregated')
        path = db.save_dataset(df, tag=tag)
        return {'dataset_path': path, 'n_samples': len(df), 'log': log}

    run_task(tid, do_aggregate)
    return jsonify({'task_id': tid})

@app.route('/api/ml-dataset/generate-scripts', methods=['POST'])
def ml_dataset_generate_scripts():
    data = request.json or {}
    try:
        from pipeline_runner import resolve_pipeline_config, estimate_pipeline_tasks, DEFAULT_FIXED_HALFWIDTH_DEG
        cfg = resolve_pipeline_config(data)
        run_id = data.get('run_id') or f"ml_dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
        run_id = re.sub(r'[^A-Za-z0-9_\-]+', '_', run_id).strip('_')
        if not run_id.startswith('ml_dataset_'):
            run_id = 'ml_dataset_' + run_id

        angle_mode = cfg.get('angle_mode', 'motor')
        from dataset_builder import MOTOR_ANGLES, FULL_ANGLES
        script_angles = FULL_ANGLES if angle_mode == 'full' else MOTOR_ANGLES
        n_samples = int(cfg.get('n_samples', 24))
        sim_steps = max(4, int(cfg.get('sim_n_steps', 40)))

        tex = get_texture_module()
        batch_dir = tex.generate_batch_lhs(
            n_samples=n_samples,
            f_Goss_range=cfg.get('f_Goss_range', [0.4, 0.9]),
            theta_0_range=cfg.get('theta_0_range', [1, 30]),
            halfwidth_range=cfg.get('halfwidth_range', [DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG]),
            N_grains_range=cfg.get('N_grains_range', [8, 8]),
            Si_content=cfg.get('Si_content', 3.0),
            output_dir=f'preinput/{run_id}',
        )

        old_steps = gis.SimulationConfig.N_STEPS
        try:
            gis.SimulationConfig.N_STEPS = sim_steps
            if 'sim_h_max' in cfg:
                gis.SimulationConfig.H_MAX = float(cfg.get('sim_h_max'))
            script_dir = f'grain_scripts/{run_id}'
            txt_files = sorted(Path(batch_dir).glob('grain_orientations_ODF_*.txt'))
            for tf in txt_files:
                with contextlib.redirect_stdout(io.StringIO()):
                    gis.generate_scripts_for_config(str(tf), angles=script_angles, output_dir=script_dir)
        finally:
            gis.SimulationConfig.N_STEPS = old_steps

        configs_for_batch = []
        for name, cfg_item, angles in gis.get_configs_in_dir(f'grain_scripts/{run_id}'):
            cfg_item = dict(cfg_item)
            cfg_item['source_dir'] = f'grain_scripts\\{name}'
            cfg_item['output_name'] = Path(name).name
            configs_for_batch.append((name, cfg_item, angles))
        Path('scripts').mkdir(exist_ok=True)
        batch_script = f'scripts/run_{run_id}.ps1'
        with contextlib.redirect_stdout(io.StringIO()):
            gpb.generate_multi_config_powershell_script(
                configs_for_batch, batch_script, run_name=f'run_{run_id}'
            )

        manifest = {
            'run_id': run_id,
            'recommended_output_run': f'run_{run_id}',
            'batch_dir': batch_dir,
            'script_dir': f'grain_scripts/{run_id}',
            'batch_script': batch_script,
            'preset_id': cfg.get('preset_id') or cfg.get('preset'),
            'preset_label': cfg.get('preset_label'),
            'config': cfg,
            'angles': script_angles,
            'n_samples': n_samples,
            'estimated_tasks': estimate_pipeline_tasks(cfg),
            'created': datetime.now().isoformat(),
            'purpose': 'machine_learning_dataset_generation',
        }
        manifest_path = f'scripts/{run_id}_manifest.json'
        Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return jsonify({**manifest, 'manifest_path': manifest_path})
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/ml-dataset/presets', methods=['GET'])
def ml_dataset_presets():
    try:
        from pipeline_runner import get_pipeline_presets
        presets = get_pipeline_presets()
        result = {}
        for key, preset in presets.items():
            item = dict(preset)
            item['label'] = {
                'smoke': 'Dataset Smoke / 冒烟数据集',
                'lite': 'Dataset Lite / 轻量数据集',
                'std': 'Dataset Std / 标准数据集',
                'max': 'Dataset Max / 全量数据集',
            }.get(key, preset.get('label', key))
            item['kind'] = 'dataset_generation'
            item['description'] = '仅控制 ML 专用仿真数据集规模、角度、晶粒数与仿真步数；不控制模型训练超参数。'
            result[key] = item
        return jsonify({'presets': result})
    except Exception as e:
        fallback = {
            'smoke': {'n_samples': 6, 'n_grains': 4, 'sim_n_steps': 8},
            'lite': {'n_samples': 24, 'n_grains': 8, 'sim_n_steps': 40},
            'std': {'n_samples': 64, 'n_grains': 16, 'sim_n_steps': 100},
            'max': {'n_samples': 128, 'n_grains': 32, 'sim_n_steps': 120},
        }
        labels = {
            'smoke': 'Dataset Smoke / 冒烟数据集',
            'lite': 'Dataset Lite / 轻量数据集',
            'std': 'Dataset Std / 标准数据集',
            'max': 'Dataset Max / 全量数据集',
        }
        presets = {}
        for key, cfg in fallback.items():
            presets[key] = {
                'id': key,
                'label': labels[key],
                'kind': 'dataset_generation',
                'description': '仅控制 ML 专用仿真数据集规模、角度、晶粒数与仿真步数；不控制模型训练超参数。',
                'metric_expectation': 'exploratory_only' if key == 'smoke' else 'holdout_validation',
                'config': {
                    'n_samples': cfg['n_samples'],
                    'angle_mode': 'motor',
                    'f_Goss_range': [0.4, 0.9],
                    'theta_0_range': [1, 30],
                    'halfwidth_range': [10.0, 10.0],
                    'N_grains_range': [cfg['n_grains'], cfg['n_grains']],
                    'Si_content': 3.0,
                    'sim_n_steps': cfg['sim_n_steps'],
                },
                'estimate': {
                    'n_samples': cfg['n_samples'],
                    'n_angles': 2,
                    'angles': [0, 90],
                    'n_grains_est': cfg['n_grains'],
                    'mumax3_tasks': cfg['n_samples'] * 2 * cfg['n_grains'],
                    'sim_n_steps': cfg['sim_n_steps'],
                },
                'warning': str(e),
            }
        return jsonify({'presets': presets, 'warning': str(e)})


# -- Dataset / ML simple presets (pipeline 页面用) -------------------------

DATASET_PRESETS = {
    'smoke': {'n_samples': 4,   'f_Goss_range': [0.5, 0.8], 'theta_0_range': [0, 30],
              'N_grains': 5, 'halfwidth': 10, 'label': 'Smoke (4 样本)'},
    'lite':  {'n_samples': 20,  'f_Goss_range': [0.35, 0.95], 'theta_0_range': [0, 45],
              'N_grains': 5, 'halfwidth': 10, 'label': 'Lite (20 样本)'},
    'std':   {'n_samples': 60,  'f_Goss_range': [0.35, 0.95], 'theta_0_range': [0, 45],
              'N_grains': 5, 'halfwidth': 10, 'label': 'Std (60 样本)'},
    'max':   {'n_samples': 150, 'f_Goss_range': [0.35, 0.95], 'theta_0_range': [0, 45],
              'N_grains': 5, 'halfwidth': 10, 'label': 'Max (150 样本)'},
}

ML_TRAIN_PRESETS = {
    'smoke':       {'model_type': 'direct_xgb', 'n_estimators': 50,  'label': 'Smoke'},
    'lite':        {'model_type': 'direct_xgb', 'n_estimators': 150, 'label': 'Lite'},
    'std':         {'model_type': 'direct_xgb', 'n_estimators': 300, 'label': 'Std ⭐'},
    'max':         {'model_type': 'direct_xgb', 'n_estimators': 600, 'label': 'Max'},
    'extra_trees': {'model_type': 'extra_trees','n_estimators': 300, 'label': 'ExtraTrees'},
    'custom':      {'model_type': 'direct_xgb', 'n_estimators': 300, 'label': 'Custom'},
}

@app.route('/api/dataset/presets', methods=['GET'])
def dataset_presets():
    return jsonify({'presets': DATASET_PRESETS})

@app.route('/api/ml/presets', methods=['GET'])
def ml_train_presets():
    return jsonify({'presets': ML_TRAIN_PRESETS})

# -- ODF figures viewer ---------------------------------------------------

@app.route('/api/odf/figures', methods=['GET'])
def odf_figures():
    """扫描 preinput/ 目录，返回含 odf_figures/ 子目录的批次列表。"""
    preinput = Path('preinput')
    items = []
    if not preinput.exists():
        return jsonify({'items': items})
    for subdir in sorted(preinput.iterdir()):
        if not subdir.is_dir():
            continue
        fig_dir = subdir / 'odf_figures'
        has_odf = fig_dir.is_dir() and any(fig_dir.glob('*.png'))
        figures = []
        if has_odf:
            for f in sorted(fig_dir.glob('*.png')):
                figures.append({'name': f.name, 'path': _posix(f)})
        items.append({'folder': subdir.name, 'has_odf': has_odf, 'figures': figures})
    # 根目录下直接的 odf_figures/
    root_fig_dir = preinput / 'odf_figures'
    if root_fig_dir.is_dir():
        figs = [{'name': f.name, 'path': _posix(f)} for f in sorted(root_fig_dir.glob('*.png'))]
        if figs:
            items.insert(0, {'folder': '(根目录)', 'has_odf': True, 'figures': figs})
    return jsonify({'items': items})

@app.route('/api/odf/preview', methods=['GET'])
def odf_preview():
    """内联预览 ODF PNG（不触发下载）。路径必须在 preinput/ 下且为 .png。"""
    img_path = request.args.get('path', '')
    p = Path(os.path.normpath(img_path))
    if p.suffix.lower() != '.png':
        return jsonify({'error': '只支持 PNG 预览'}), 400
    parts = p.parts
    if 'preinput' not in parts and 'odf_figures' not in parts:
        return jsonify({'error': '路径安全限制：只能预览 preinput/ 下的 ODF 图片'}), 403
    if not p.exists():
        return jsonify({'error': '文件不存在'}), 404
    return send_file(str(p.resolve()), mimetype='image/png')

# -- ML train / infer -----------------------------------------------------

@app.route('/api/ml/train', methods=['POST'])
def ml_train():
    data = request.json or {}
    ds   = data.get('dataset_path')
    if not ds or not Path(ds).exists():
        return jsonify({'error': 'dataset file not found'}), 400
    model_type = data.get('model_type', 'direct_xgb')
    tid = create_task('model_train')
    run_task(tid, _get_ml().train,
             ds,
             model_type=model_type,
             xgb_params=data.get('xgb_params'),
             test_size=float(data.get('test_size', 0.2)))
    return jsonify({'task_id': tid})

@app.route('/api/ml/paper-presets', methods=['GET'])
def ml_paper_presets():
    try:
        from paper_surrogate_trainer import get_paper_training_presets
        return jsonify({'presets': get_paper_training_presets()})
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/ml/paper-train', methods=['POST'])
def ml_paper_train():
    data = request.json or {}
    ds = data.get('dataset_path')
    if not ds or not Path(ds).exists():
        return jsonify({'error': 'dataset file not found'}), 400
    from paper_surrogate_trainer import PaperSurrogateTrainer, resolve_paper_training_config
    cfg = resolve_paper_training_config(data)
    tid = create_task('paper_surrogate_model_selection')

    def do_train():
        trainer = PaperSurrogateTrainer(
            output_dir=data.get('output_dir', 'data/paper_models'),
            random_state=int(cfg.get('random_state', 42)),
            xgb_params=cfg.get('xgb_params'),
            extra_trees_params=cfg.get('extra_trees_params'),
            pca_variance=float(cfg.get('pca_variance', 0.999)),
            pca_max_components=cfg.get('pca_max_components'),
            candidate_models=cfg.get('candidate_models'),
        )
        return trainer.run(
            ds,
            target_scope=cfg.get('target_scope', 'bh_only'),
            min_samples=int(cfg.get('min_samples', 24)),
            test_size=float(cfg.get('test_size', 0.2)),
            n_splits=int(cfg.get('n_splits', 5)),
            preset_id=cfg.get('preset_id'),
            preset_label=cfg.get('preset_label'),
        )

    run_task(tid, do_train)
    return jsonify({'task_id': tid, 'preset': cfg.get('preset_id')})

@app.route('/api/ml/models', methods=['GET'])
def ml_models():
    try:
        return jsonify({'models': _get_ml().list_models()})
    except Exception as e:
        return jsonify({'models': [], 'warning': str(e), 'traceback': traceback.format_exc()})

@app.route('/api/ml/predict', methods=['POST'])
def ml_predict():
    data     = request.json or {}
    model_id = data.get('model_id')
    params   = data.get('params', {})
    try:
        pred = _get_ml()
        if model_id:
            pred.load(model_id)
        result = pred.predict_bh(params)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/ml/models/<model_id>', methods=['DELETE'])
def ml_delete_model(model_id):
    ok = _get_ml().delete_model(model_id)
    return jsonify({'success': ok})


# -- Maxwell export --------------------------------------------------------

@app.route('/api/analyze/export-maxwell', methods=['POST'])
def export_maxwell():
    data      = request.json or {}
    H_RD      = data.get('H_RD', [])
    B_RD      = data.get('B_RD', [])
    H_TD      = data.get('H_TD')
    B_TD      = data.get('B_TD')
    mat_name   = data.get('mat_name') or 'GO_Sim_%s' % datetime.now().strftime('%Y%m%d_%H%M%S')
    thickness  = float(data.get('thickness_mm', 0.35))
    si_content = float(data.get('Si_content', 3.0))
    try:
        mx = _get_mx()
        path = mx.export_from_bh_curves(
            H_RD, B_RD, H_TD, B_TD, mat_name=mat_name,
            thickness_mm=thickness, source='analysis_api',
            si_content=si_content
        )
        return send_file(
            path,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='%s.amat' % mat_name
        )
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/analyze/export-maxwell-from-prediction', methods=['POST'])
def export_maxwell_from_prediction():
    data      = request.json or {}
    model_id  = data.get('model_id')
    params    = data.get('params', {})
    mat_name  = data.get('mat_name') or 'GO_Pred_%s' % datetime.now().strftime('%Y%m%d_%H%M%S')
    thickness = float(data.get('thickness_mm', 0.35))
    try:
        pred = _get_ml()
        if model_id:
            pred.load(model_id)
        result  = pred.predict_bh(params)
        result['model_id'] = model_id
        mx = _get_mx()
        path = mx.export_from_prediction(
            result, mat_name=mat_name, thickness_mm=thickness
        )
        return send_file(
            path,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='%s.amat' % mat_name
        )
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/analyze/exports', methods=['GET'])
def list_maxwell_exports():
    try:
        return jsonify({'exports': _get_mx().list_exports()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bh-analysis/reference-list', methods=['GET'])
def bh_analysis_reference_list():
    """返回所有可用于对比图的材料列表（参考等级 + data/exports/ 历史仿真）。"""
    try:
        import bh_curve_analyzer as bhca
        materials = bhca.list_available_materials()
        return jsonify({'materials': materials})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze/apply-delta-correction', methods=['POST'])
def api_apply_delta_correction():
    """对原始仿真聚合 B-H 曲线施加参考修正（RD/TD 方向均支持）。"""
    import numpy as np
    from modules.reference_corrector import apply_reference_correction, get_td_scale
    data = request.json or {}
    direction = data.get('direction', 'RD').upper()
    H   = np.array(data.get('H', []),  dtype=float)
    B   = np.array(data.get('B', []),  dtype=float)
    odf = {
        'f_Goss':        float(data.get('f_goss',      0.8)),
        'theta_0_deg':   float(data.get('theta_0_deg', 6.0)),
        'halfwidth_deg': float(data.get('halfwidth_deg', 8.0)),
    }
    weight_cap  = float(data.get('weight_cap', 1.0))
    si_content  = float(data.get('si_content', 3.0))
    hc_sim_raw  = data.get('hc_sim_median', None)
    hc_sim      = float(hc_sim_raw) if hc_sim_raw is not None else None
    try:
        B_corr = apply_reference_correction(
            H, B, odf, direction=direction, weight_cap=weight_cap,
            hc_sim=hc_sim, si_content=si_content,
        )
        resp = {'H': H.tolist(), 'B_corrected': B_corr.tolist()}
        if direction == 'TD':
            resp['scale_td'] = round(get_td_scale(H, B, odf), 6)
        return jsonify(resp)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/analyze/export-bh-analysis', methods=['POST'])
def export_bh_analysis():
    """
    生成 .amat 文件（AEDT 格式）并运行 BH 曲线宏观参数分析，
    对比 go_steel_data/output/ 中的参考等级，生成三张对比图。
    返回 JSON 包含 metrics / core_loss / plot 路径。
    """
    data         = request.json or {}
    name         = data.get('name') or 'GO_Sim_%s' % datetime.now().strftime('%Y%m%d_%H%M%S')
    rd_H         = data.get('rd_H', [])
    rd_B         = data.get('rd_B', [])
    td_H         = data.get('td_H') or rd_H
    td_B         = data.get('td_B') or rd_B
    thickness_mm = float(data.get('thickness_mm', 0.35))
    include_names = data.get('include_names')  # list[str] | None
    try:
        import bh_curve_analyzer as bhca

        mx = _get_mx()
        amat_content = mx.generate_amat_content(
            name, rd_H, rd_B, td_H, td_B, thickness_mm=thickness_mm)
        amat_path = mx.save_amat_file(amat_content, name)

        export_dir = os.path.join(os.getcwd(), 'data', 'exports')
        analysis   = bhca.analyze_bh_pair(
            name, rd_H, rd_B, td_H, td_B,
            save_dir=export_dir, thickness_mm=thickness_mm,
            include_names=include_names,
        )

        def to_web(abs_path):
            rel = os.path.relpath(abs_path, export_dir).replace('\\', '/')
            return f'/data/exports/{rel}'

        return jsonify({
            'amat_filename':  '%s.amat' % name,
            'amat_web_path':  '/data/exports/%s.amat' % name,
            'plots': {k: to_web(v) for k, v in analysis['plots'].items()},
            'metrics_rd':  analysis['metrics_rd'],
            'metrics_td':  analysis['metrics_td'],
            'core_loss':   analysis['core_loss'],
            'csv_web_path':  to_web(analysis['csv_path']),
            'json_web_path': to_web(analysis['json_path']),
            'n_reference_materials': analysis.get('n_reference_materials', 0),
        })
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/data/exports/<path:filename>')
def serve_export_file(filename):
    """静态文件服务：data/exports/ 下的 .amat / .png / .csv / .json。"""
    from flask import send_from_directory
    export_dir = os.path.join(os.getcwd(), 'data', 'exports')
    return send_from_directory(export_dir, filename)


# -- pipeline -------------------------------------------------------------

@app.route('/api/pipeline/presets', methods=['GET'])
def pipeline_presets():
    from pipeline_runner import get_pipeline_presets
    return jsonify({'presets': get_pipeline_presets()})

@app.route('/api/pipeline/start', methods=['POST'])
def pipeline_start():
    config = request.json or {}
    pid    = _get_pr().start(config)
    return jsonify({'pipeline_id': pid})

@app.route('/api/pipeline/<pid>/state', methods=['GET'])
def pipeline_state(pid):
    s = _get_pr().get_state(pid)
    if s is None:
        return jsonify({'error': 'pipeline not found'}), 404
    return jsonify(s)

@app.route('/api/pipeline/<pid>/stream')
def pipeline_stream(pid):
    def gen():
        for chunk in _get_pr().event_stream(pid):
            yield chunk
    return Response(stream_with_context(gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})

@app.route('/api/pipeline/<pid>/resume', methods=['POST'])
def pipeline_resume(pid):
    ok = _get_pr().resume_after_sim(pid)
    return jsonify({'success': ok,
                    'error': None if ok else 'pipeline not in waiting_sim state'})

@app.route('/api/pipeline/list', methods=['GET'])
def pipeline_list():
    return jsonify({'pipelines': _get_pr().list_pipelines()})


if __name__ == '__main__':
    print("="*60)
    print("Fe-Si Multi-Crystal Micromagnetics Platform v2")
    print("Workspace: %s" % os.getcwd())
    print("URL: http://127.0.0.1:5000")
    print("="*60)
    app.run(debug=True, port=5000, threaded=True)
