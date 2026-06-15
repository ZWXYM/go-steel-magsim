"""
Fe-Si 多晶微磁学仿真集成平台 - Flask Backend v2
"""
import sys, os, threading, uuid, json, io, traceback, re, shutil, subprocess, time, zipfile as zf_mod2
import locale
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file, Response, stream_with_context

import matplotlib
matplotlib.use('Agg')

app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import mx3_generator as gis
import batch_scheduler as gpb
import bh_extractor as see_module

import importlib.util
_texture_module = None
def get_texture_module():
    global _texture_module
    if _texture_module is None:
        spec = importlib.util.spec_from_file_location("texture_gen", os.path.join(SCRIPT_DIR, "odf_texture.py"))
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
        generated_files = []
        if script_type in ['1','5']:
            f = f"{base_name}.bat"; gpb.generate_multi_config_batch_script(selected_configs, f); generated_files.append(f)
        if script_type in ['2','5']:
            f = f"{base_name}.ps1"; gpb.generate_multi_config_powershell_script(selected_configs, f); generated_files.append(f)
        if script_type in ['3','5']:
            f = f"{base_name}.sh"; gpb.generate_multi_config_bash_script(selected_configs, f); generated_files.append(f)
        if script_type == '4' and mumax3_path:
            f = f"{base_name}_custom.sh"; gpb.generate_multi_config_bash_script(selected_configs, f, mumax3_path); generated_files.append(f)
        total_tasks = sum(d['n_grains'] * len(c) for _, d, c in selected_configs)
        return jsonify({'success': True, 'files': generated_files, 'total_tasks': total_tasks})
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

# ── Task Scripts Management ──────────────────────────────────
@app.route('/api/task-scripts', methods=['GET'])
def list_task_scripts():
    scripts = []
    for ext in ['*.bat','*.ps1','*.sh']:
        for f in Path('.').glob(ext):
            if not f.name.startswith('run_'): continue
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
                            'configs': configs, 'angles': angles_info, 'path': str(f)})
    return jsonify({'scripts': sorted(scripts, key=lambda x: x['created'], reverse=True)})

@app.route('/api/task-scripts/<filename>', methods=['DELETE'])
def delete_task_script(filename):
    p = Path(filename)
    if p.exists() and p.suffix in ['.bat','.ps1','.sh']:
        p.unlink(); return jsonify({'success': True})
    return jsonify({'error': '文件不存在'}), 404

@app.route('/api/task-scripts/<filename>/preview', methods=['GET'])
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
    Msat = float(request.form.get('Msat', 1.52e6))
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
        config_info = {'name': run_dir.name, 'angles': []}
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
            config_info = {'name': subdir.name, 'angles': []}
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
    Msat = float(data.get('Msat', 1.52e6))
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
                pm = re.search(r'Global Progress: (\d+)/(\d+)', line)
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
    mode    = data.get('angle_mode', 'motor')
    Msat    = float(data.get('Msat', 1.52e6))
    from dataset_builder import MOTOR_ANGLES, FULL_ANGLES
    target_angles = MOTOR_ANGLES if mode == 'motor' else FULL_ANGLES
    tag     = data.get('tag', mode)
    tid     = create_task('dataset_aggregate')

    def do_aggregate():
        db = _get_db()
        log = []
        def cb(cur, tot, name):
            log.append('(%d/%d) %s' % (cur, tot, name))
        df   = db.build_dataset(configs=configs, target_angles=target_angles,
                                Msat=Msat, progress_callback=cb)
        path = db.save_dataset(df, tag=tag)
        return {'dataset_path': path, 'n_samples': len(df), 'log': log}

    run_task(tid, do_aggregate)
    return jsonify({'task_id': tid})


# -- ML train / infer -----------------------------------------------------

@app.route('/api/ml/train', methods=['POST'])
def ml_train():
    data = request.json or {}
    ds   = data.get('dataset_path')
    if not ds or not Path(ds).exists():
        return jsonify({'error': 'dataset file not found'}), 400
    tid = create_task('xgboost_train')
    run_task(tid, _get_ml().train,
             ds,
             xgb_params=data.get('xgb_params'),
             test_size=float(data.get('test_size', 0.2)))
    return jsonify({'task_id': tid})

@app.route('/api/ml/models', methods=['GET'])
def ml_models():
    try:
        return jsonify({'models': _get_ml().list_models()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    mat_name  = data.get('mat_name') or 'GO_Sim_%s' % datetime.now().strftime('%Y%m%d_%H%M%S')
    thickness = float(data.get('thickness_mm', 0.35))
    try:
        mx      = _get_mx()
        content = mx.generate_amat_content(mat_name, H_RD, B_RD, H_TD, B_TD,
                                           thickness_mm=thickness)
        mx.save_amat_file(content, mat_name)
        return send_file(
            io.BytesIO(content.encode('utf-8')),
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
        mx      = _get_mx()
        content = mx.generate_amat_content(
            mat_name,
            result['RD']['H'], result['RD']['B'],
            result['TD']['H'], result['TD']['B'],
            thickness_mm=thickness
        )
        mx.save_amat_file(content, mat_name)
        return send_file(
            io.BytesIO(content.encode('utf-8')),
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


# -- pipeline -------------------------------------------------------------

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
