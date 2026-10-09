"""Browser entrypoints for source-bound calibration and isolated motor scans."""
import json
import os
from pathlib import Path

from flask import Blueprint,jsonify,request,render_template,send_file
from modules.calibration_workbench import CalibrationWorkbench
from modules.motor_workbench import MotorWorkbench
from modules.workbench_cpu_policy import install_cpu_policy
from modules.system_runtime import runtime_identity
from modules.motor_optimization import MotorOptimization
from modules.motor_failure_diagnostics import diagnose_job,append_export_diagnostics
from modules.optimization_plans import OptimizationPlans
from modules.motor_waveforms import MotorWaveforms
from modules.material_library import MaterialLibrary
from modules.calibrated_motor_preparation import CalibratedMotorPreparation
from modules.calibrated_motor_native import CalibratedMotorNative
from modules.calibrated_motor_analysis import CalibratedMotorAnalysis
from modules.calibrated_motor_execution import CalibratedMotorExecution


def create_workbench(project,storage=None,root=None):
    project=Path(project).resolve()
    root=Path(root or os.environ.get('GO_STEEL_THESIS_ROOT') or
        (project.parent if project.name=='magsim' and (project.parent/'calibration/pilot_20261003_n8/manifest.json').is_file() else project)).resolve()
    storage=Path(storage or os.environ.get('MAGSIM_WORKBENCH_DIR') or project/'data/workbench').resolve()
    calibration=CalibrationWorkbench(root,storage/'calibration')
    motor=MotorWorkbench(root,storage/'motor',project)
    optimization=MotorOptimization(root,storage/'optimization',motor)
    plans=OptimizationPlans(optimization)
    waveforms=MotorWaveforms(motor)
    materials=MaterialLibrary(calibration)
    calibrated_motor=CalibratedMotorPreparation(materials,motor,storage/'calibrated_motor')
    native_material=CalibratedMotorNative(calibrated_motor,motor,storage/'calibrated_motor_native')
    bh_analysis=CalibratedMotorAnalysis(native_material,waveforms,storage/'calibrated_motor_analysis')
    bh_execution=CalibratedMotorExecution(bh_analysis,motor,storage/'calibrated_motor_execution')
    identity=runtime_identity(project,root,storage)
    bp=Blueprint('workbench',__name__)
    if os.environ.get('MAGSIM_CPU_ONLY')=='1':
        bp.record_once(lambda state:install_cpu_policy(state.app))

    @bp.errorhandler(ValueError)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(FileNotFoundError)
    def bad_input(error):
        return jsonify(error=str(error)),400

    @bp.get('/workbench')
    def page():
        return render_template('workbench.html')

    @bp.get('/system')
    def system_page():
        return render_template('system.html')

    @bp.get('/material-library')
    def material_page():
        return render_template('material_library.html')

    @bp.get('/api/workbench/material-library')
    def material_catalog():
        return jsonify(materials.catalog())

    @bp.get('/api/workbench/material-library/<artifact>/<key>')
    def material_detail(artifact,key):
        return jsonify(materials.detail(artifact,key))

    @bp.get('/api/workbench/material-library/<artifact>/<key>/files/<name>')
    def material_file(artifact,key,name):
        return send_file(materials.download(artifact,key,name),as_attachment=True,download_name=name)

    @bp.get('/api/workbench/material-library/<artifact>/<key>/bundle')
    def material_bundle(artifact,key):
        return send_file(materials.bundle(artifact,key),as_attachment=True,
                         download_name=artifact+'_'+key.replace(':','_')+'.zip',mimetype='application/zip')

    @bp.get('/api/workbench/calibrated-motor')
    def calibrated_motor_records():
        return jsonify(calibrated_motor.records())

    @bp.post('/api/workbench/calibrated-motor')
    def calibrated_motor_prepare():
        return jsonify(calibrated_motor.prepare(request.get_json()))

    @bp.get('/api/workbench/calibrated-motor/<artifact>')
    def calibrated_motor_record(artifact):
        return jsonify(calibrated_motor.get(artifact))

    @bp.get('/api/workbench/calibrated-motor/<artifact>/files/<name>')
    def calibrated_motor_download(artifact,name):
        return send_file(calibrated_motor.download(artifact,name),as_attachment=True)

    @bp.get('/api/workbench/calibrated-motor/native-imports')
    def native_material_records():
        return jsonify(native_material.records())

    @bp.post('/api/workbench/calibrated-motor/native-imports')
    def native_material_prepare():
        return jsonify(native_material.prepare(request.get_json()))

    @bp.get('/api/workbench/calibrated-motor/native-imports/<artifact>')
    def native_material_record(artifact):
        return jsonify(native_material.get(artifact))

    @bp.post('/api/workbench/calibrated-motor/native-imports/<artifact>/start')
    def native_material_start(artifact):
        bh_execution.ensure_idle()
        return jsonify(native_material.start(artifact))

    @bp.get('/api/workbench/calibrated-motor/native-imports/<artifact>/files/<name>')
    def native_material_download(artifact,name):
        return send_file(native_material.download(artifact,name),as_attachment=True)

    @bp.get('/calibrated-motor/analysis/<artifact>')
    def bh_analysis_page(artifact):
        bh_analysis.path(artifact)
        return render_template('calibrated_motor_analysis.html',artifact=artifact)

    @bp.get('/api/workbench/calibrated-motor/analysis-plans')
    def bh_analysis_records():
        return jsonify(bh_analysis.records())

    @bp.post('/api/workbench/calibrated-motor/analysis-plans')
    def bh_analysis_prepare():
        return jsonify(bh_analysis.prepare(request.get_json()))

    @bp.get('/api/workbench/calibrated-motor/analysis-plans/<artifact>')
    def bh_analysis_record(artifact):
        return jsonify(bh_analysis.get(artifact))

    @bp.post('/api/workbench/calibrated-motor/analysis-plans/<artifact>/prepare-model')
    def bh_analysis_model(artifact):
        return jsonify(bh_analysis.prepare_model(artifact))

    @bp.get('/api/workbench/calibrated-motor/analysis-plans/<artifact>/bundle')
    def bh_analysis_bundle(artifact):
        return send_file(bh_analysis.bundle(artifact),as_attachment=True,download_name=artifact+'.zip',mimetype='application/zip')

    @bp.get('/api/workbench/calibrated-motor/executions')
    def bh_execution_records():
        return jsonify(bh_execution.records())

    @bp.post('/api/workbench/calibrated-motor/executions')
    def bh_execution_prepare():
        return jsonify(bh_execution.prepare(request.get_json()))

    @bp.get('/api/workbench/calibrated-motor/executions/<artifact>')
    def bh_execution_record(artifact):
        return jsonify(bh_execution.get(artifact))

    @bp.post('/api/workbench/calibrated-motor/executions/<artifact>/start')
    def bh_execution_start(artifact):
        return jsonify(bh_execution.start(artifact))

    @bp.get('/api/workbench/calibrated-motor/executions/<artifact>/files/<path:name>')
    def bh_execution_file(artifact,name):
        return send_file(bh_execution.download(artifact,name),as_attachment=True,download_name=Path(name).name)

    @bp.get('/api/workbench/calibrated-motor/executions/<artifact>/bundle')
    def bh_execution_bundle(artifact):
        return send_file(bh_execution.bundle(artifact),as_attachment=True,download_name=artifact+'.zip',mimetype='application/zip')

    @bp.get('/api/workbench/calibrated-motor/torque-reference/<dataset>/<case>')
    def bh_torque_reference(dataset,case):
        return jsonify(bh_analysis.reference(dataset,case))

    @bp.get('/api/workbench/calibrated-motor/torque-reference/<dataset>/<case>/csv')
    def bh_torque_reference_csv(dataset,case):
        return send_file(bh_analysis.reference_csv(dataset,case),as_attachment=True,download_name=dataset+'_'+case+'_torque.csv',mimetype='text/csv')

    @bp.get('/motor-optimization')
    def optimization_page():
        return render_template('motor_optimization.html')

    @bp.get('/motor-waveforms')
    def waveform_page():
        return render_template('motor_waveforms.html')

    @bp.get('/api/workbench/waveforms/catalog')
    def waveform_catalog():
        return jsonify(waveforms.catalog())

    @bp.post('/api/workbench/waveforms/compare')
    def waveform_compare():
        return jsonify(waveforms.compare(request.get_json())[0])

    @bp.post('/api/workbench/waveforms/export')
    def waveform_export():
        return send_file(waveforms.export(request.get_json()),as_attachment=True,
                         download_name='official_motor_waveforms.zip',mimetype='application/zip')

    @bp.get('/api/workbench/optimization/workflow')
    def optimization_workflow():
        return jsonify(optimization.workflow())

    @bp.get('/api/workbench/optimization/sources/<source>/<name>')
    def optimization_source(source,name):
        return send_file(optimization.source_file(source,name),as_attachment=True)

    @bp.get('/api/workbench/optimization/datasets')
    def optimization_datasets():
        return jsonify(optimization.datasets())

    @bp.get('/api/workbench/optimization/datasets/<dataset_id>')
    def optimization_dataset(dataset_id):
        return jsonify(optimization.dataset(dataset_id))

    @bp.post('/api/workbench/optimization/analyze')
    def optimization_analyze():
        return jsonify(optimization.analyze(request.get_json()))

    @bp.get('/api/workbench/optimization/history')
    def optimization_history():
        return jsonify(optimization.history())

    @bp.get('/api/workbench/optimization/<artifact_id>/files/<name>')
    def optimization_file(artifact_id,name):
        return send_file(optimization.artifact(artifact_id,name),as_attachment=True)

    @bp.get('/api/workbench/optimization/plan-catalog')
    def plan_catalog():
        return jsonify(dict(motor.catalog(),cpu_only=os.environ.get('MAGSIM_CPU_ONLY')=='1'))

    @bp.get('/api/workbench/optimization/plans')
    def optimization_plans():
        return jsonify(plans.records())

    @bp.post('/api/workbench/optimization/plans')
    def prepare_optimization_plan():
        return jsonify(plans.prepare(request.get_json()))

    @bp.post('/api/workbench/optimization/plans/<plan_id>/submit')
    def submit_optimization_plan(plan_id):
        bh_execution.ensure_idle()
        return jsonify(plans.submit(plan_id))

    @bp.post('/api/workbench/optimization/plans/<plan_id>/refresh')
    def refresh_optimization_plan(plan_id):
        return jsonify(plans.refresh(plan_id))

    @bp.get('/api/workbench/runtime')
    def runtime():
        return jsonify(identity)

    @bp.get('/api/workbench/samples')
    def samples():
        return jsonify(calibration.samples(include_quality=True))

    @bp.get('/api/workbench/resources')
    def resources():
        cpu_only=os.environ.get('MAGSIM_CPU_ONLY')=='1'
        return jsonify(cpu_only=cpu_only,native_submission_allowed=not cpu_only,training_submission_allowed=not cpu_only)

    @bp.get('/api/workbench/samples/<grade>/raw-pair')
    def raw_pair(grade):
        if grade not in ('B23R075','B27R090','B27R095','B30P105'):
            raise ValueError('未知样品')
        return jsonify(calibration.compatible_pair(json.loads((calibration.pilot/grade/'raw_pair.json').read_text(encoding='utf-8'))))

    @bp.post('/api/workbench/calibrate')
    def calibrate():
        data=request.get_json()
        return jsonify(calibration.calibrate(data['grades'],data.get('calibration_version','fixed_h_delta_v1')))

    @bp.get('/api/workbench/calibrations')
    def calibrations():
        paths=sorted((storage/'calibration').glob('cal_*/report.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        return jsonify([json.loads(p.read_text(encoding='utf-8')) for p in paths])

    @bp.get('/api/workbench/calibrations/<artifact>')
    def calibration_report(artifact):
        return jsonify(json.loads((calibration.get(artifact)/'report.json').read_text(encoding='utf-8')))

    @bp.post('/api/workbench/calibrations/<artifact>/predict')
    def predict(artifact):
        return jsonify(calibration.predict(artifact,request.get_json()))

    @bp.post('/api/workbench/calibrations/<artifact>/predict-excluded')
    def predict_excluded(artifact):
        data=request.get_json()
        if not isinstance(data,dict) or not data.get('exclude_grades'):
            raise ValueError('完整材料排除需要明确选择牌号')
        return jsonify(calibration.predict(artifact,data['raw_pair'],data['exclude_grades']))

    @bp.get('/api/workbench/motor/catalog')
    def motor_catalog():
        return jsonify(motor.catalog())

    @bp.get('/api/workbench/motor/archive')
    def motor_archive():
        return jsonify(motor.archived_results())

    @bp.get('/api/workbench/motor/archive/<case_id>/files/<path:name>')
    def motor_archive_file(case_id,name):
        return send_file(motor.archive_file(case_id,name),as_attachment=True)

    @bp.post('/api/workbench/motor/archive/export')
    def motor_archive_export():
        return jsonify(motor.export_archive())

    @bp.get('/api/workbench/motor/archive/exports/<export_id>/<name>')
    def motor_archive_export_file(export_id,name):
        return send_file(motor.archive_export_path(export_id,name),as_attachment=True)

    @bp.get('/api/workbench/motor/jobs')
    def motor_jobs():
        return jsonify([diagnose_job(motor,j) for j in motor.jobs()])

    @bp.get('/api/workbench/motor/queue')
    def queue_state():
        return jsonify(motor.queue_status())

    @bp.post('/api/workbench/motor/queue/recover')
    def recover_queue():
        return jsonify(motor.recover_queue())

    @bp.post('/api/workbench/motor/jobs/<job_id>/continue')
    def continue_motor(job_id):
        return jsonify(motor.continue_unattempted(job_id))

    @bp.post('/api/workbench/motor/jobs/<job_id>/export')
    def export_motor(job_id):
        return jsonify(append_export_diagnostics(motor,motor.export_summary(job_id)))

    @bp.post('/api/workbench/motor/jobs')
    def prepare_motor():
        data=request.get_json()
        return jsonify(motor.prepare(data['materials'],data.get('cores',4),data.get('measurement_protocol','periodic_cycle3_v1')))

    @bp.get('/api/workbench/motor/jobs/<job_id>')
    def motor_state(job_id):
        return jsonify(diagnose_job(motor,motor.state(job_id)))

    @bp.post('/api/workbench/motor/jobs/<job_id>/submit')
    def submit_motor(job_id):
        bh_execution.ensure_idle()
        return jsonify(motor.submit(job_id))

    @bp.get('/api/workbench/files/<kind>/<artifact>/<path:name>')
    def download(kind,artifact,name):
        if kind=='calibration':
            base=calibration.get(artifact)
        elif kind=='motor':
            base=motor.path(artifact)
        else:
            raise ValueError('未知文件类别')
        path=(base/name).resolve()
        if not path.is_relative_to(base.resolve()) or path.suffix not in ('.json','.csv','.md','.amat','.txt','.log','.zip'):
            raise ValueError('文件路径不属于该任务')
        return send_file(path,as_attachment=True)

    if os.environ.get('MAGSIM_CPU_ONLY') != '1' and os.environ.get('MAGSIM_AUTO_RESUME_QUEUE','1')=='1' and ((storage/'motor/queue.lock').exists() or any(j['status'] in ('queued','starting','running') for j in motor.jobs())):
        motor.start_queue_monitor()
    plans.start_monitor()  # CPU derivation only; never dispatches or retries native tasks.
    return bp
