"""Isolated CPU preparation and bounded serial MuMax execution for one material."""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path
import numpy as np
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.native_material_runs import read,verify_run,BUDGET
from modules.material_calibration import file_hash,PHYSICS_VERSION
from modules.motor_workbench import write_json
from modules.motor_queue import QueueLease,process_identity
from modules.texture_sampling import SAMPLING_VERSION,LEGACY_SAMPLING_VERSION,sample_texture
from modules.mx3_generator import SimulationConfig
from tools.run_calibration_pilot import H_GRID,pilot_script,write_csv,run_jobs


def prepare(folder):
    m=verify_run(folder,PROJECT,dispatch=True);recipe=m['recipe'];p=recipe['params']
    run=folder/'run';run.mkdir();grade=recipe['material_id'];material_dir=run/grade;material_dir.mkdir()
    if m['texture_sampling_version']==SAMPLING_VERSION:
        eulers,samples,metadata=sample_texture(p['f_Goss'],p['theta_0_deg'],recipe['n_grains'],p['halfwidth_deg'],recipe['seed'])
    elif m['texture_sampling_version']==LEGACY_SAMPLING_VERSION:
        from modules.odf_texture import generate_texture_with_odf
        np.random.seed(recipe['seed'])
        eulers=generate_texture_with_odf(p['f_Goss'],p['theta_0_deg'],recipe['n_grains'],p['halfwidth_deg'],plot_odf=False,output_dir=str(material_dir/'texture'))
        samples=metadata=None
    else:raise ValueError('所选校准库的采样协议不支持')
    if len(eulers)!=recipe['n_grains'] or not np.isfinite(eulers).all():raise ValueError('实际生成的冻结取向数量或数值不符')
    orientations=material_dir/'orientations.csv';np.savetxt(orientations,eulers,delimiter=',',header='phi1,Phi,phi2',comments='')
    material=dict(grade=grade,params=p,seed=recipe['seed'],thickness_mm=recipe['thickness_mm'],
        orientations_sha256=file_hash(orientations),Si_status='user_parameter_not_measured_composition',
        ODF_status='generated_distribution_not_measured_EBSD')
    if samples is not None:
        path=material_dir/'grain_samples.csv';write_csv(path,samples)
        material.update(grain_samples=dict(path=path.relative_to(run).as_posix(),sha256=file_hash(path)),texture_sampling=metadata)
    # This function runs in its own CPU process; original platform globals stay unchanged.
    SimulationConfig.SI_CONTENT=p['Si_content']
    manifest=dict(physics_version=PHYSICS_VERSION,H_axis='physical_A_per_m',H_grid=H_GRID.tolist(),
        n_grains=recipe['n_grains'],seed=recipe['seed'],texture_sampling_version=m['texture_sampling_version'],
        texture_module_sha256=file_hash(PROJECT/'modules'/('texture_sampling.py' if samples is not None else 'odf_texture.py')),
        materials=[material],jobs=[],created_utc=m['created_utc'],source_role='user_defined_simulation_not_measured_grade')
    for direction,angle in [('RD',0),('TD',90)]:
        for index,euler in enumerate(eulers,1):
            script=material_dir/direction/'scripts'/f'grain_{index:03d}.mx3';script.parent.mkdir(parents=True,exist_ok=True)
            script.write_text(pilot_script(euler,angle),encoding='utf8')
            output=material_dir/direction/'raw'/f'grain_{index:03d}.out'
            manifest['jobs'].append(dict(grade=grade,direction=direction,angle=angle,grain_id=index,
                script=script.relative_to(run).as_posix(),script_sha256=file_hash(script),output=output.relative_to(run).as_posix()))
    write_json(run/'manifest.json',manifest)
    write_json(folder/'prepared.json',dict(input_hashes={path.relative_to(folder).as_posix():file_hash(path) for path in run.rglob('*') if path.is_file()},new_native_solves=0))
    write_json(folder/'state.json',dict(status='ready',completed_jobs=0,total_jobs=len(manifest['jobs']),elapsed_seconds=0,new_MuMax_solves=0))


def execute(folder,token):
    m=verify_run(folder,PROJECT,dispatch=True);state=read(folder/'state.json')
    if state.get('status')!='starting' or state.get('queue_token')!=token:raise ValueError('任务启动身份不符')
    launch=read(folder/'launches'/f'{token}.json')
    if launch['token']!=token or file_hash(launch['mumax_binary'])!=launch['mumax_binary_sha256']:raise ValueError('MuMax3程序与启动记录不符')
    lease=QueueLease(folder.parent.parent/'native_material_gpu.lease');wait_until=time.monotonic()+5
    while not lease.acquire():
        if time.monotonic()>wait_until:raise ValueError('GPU仿真租约仍占用，未执行新晶粒')
        time.sleep(.05)
    started=time.monotonic();prior=float(state.get('elapsed_seconds',0));deadline=started+max(0,BUDGET['wall_seconds']-prior)
    run=folder/'run';manifest=read(run/'manifest.json');identity=process_identity()
    def update(status,**extra):
        try:
            jobs=read(run/'run_status.json')['jobs'] if (run/'run_status.json').is_file() else []
            done=sum(j['exit_code']==0 for j in jobs)
        except (ValueError,KeyError,OSError):done=state.get('new_MuMax_solves',0)
        write_json(folder/'state.json',dict(status=status,queue_token=token,worker_identity=identity,
            completed_jobs=done,total_jobs=len(manifest['jobs']),elapsed_seconds=prior+time.monotonic()-started,new_MuMax_solves=done,**extra))
    try:
        update('running')
        while True:
            status=read(run/'run_status.json') if (run/'run_status.json').is_file() else {'jobs':[]}
            if len([j for j in status['jobs'] if j['exit_code']==0])==len(manifest['jobs']):break
            if (folder/'pause.request').exists():update('paused');return
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('达到120秒总预算，保留已完成与未完成文件，不自动重试')
            run_jobs(run,manifest,max_jobs=1,mumax=launch['mumax_binary'],job_timeout_s=min(BUDGET['job_seconds'],remaining))
            update('running')
        from modules.native_pair_exports import NativePairExports
        exporter=NativePairExports(m['resource_root'],folder.parent.parent/'native_pair_exports')
        # The selected run is registered in this same storage; no arbitrary source path.
        source=next(s for s in exporter.catalog()['sources'] if s.get('native_run_id')==m['id'])
        output=exporter.prepare(dict(source_id=source['id'],manifest_sha256=source['manifest_sha256'],grade=m['recipe']['material_id']))
        update('completed',export_id=output['id'])
    except Exception as exc:
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()))
        update('failed',error=str(exc))
        raise
    finally:lease.release()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true');parser.add_argument('--token');args=parser.parse_args()
    folder=args.folder.resolve()
    try:
        if args.prepare:prepare(folder)
        elif args.token:execute(folder,args.token)
        else:parser.error('execution requires --token')
    except Exception as exc:
        if args.prepare:write_json(folder/'state.json',dict(status='preparation_failed',error=str(exc),new_MuMax_solves=0))
        elif (folder/'state.json').is_file() and read(folder/'state.json').get('status')=='starting':
            write_json(folder/'state.json',dict(status='failed',error=str(exc),new_MuMax_solves=read(folder/'state.json').get('new_MuMax_solves',0)))
        traceback.print_exc();raise SystemExit(1)
