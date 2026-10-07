"""Browser entrypoints for source-bound calibration and isolated motor scans."""
import json
import os
from pathlib import Path

from flask import Blueprint,jsonify,request,render_template,send_file
from modules.calibration_workbench import CalibrationWorkbench
from modules.motor_workbench import MotorWorkbench
from modules.workbench_cpu_policy import install_cpu_policy


def create_workbench(project,storage=None,root=None):
    project=Path(project).resolve()
    root=Path(root or os.environ.get('GO_STEEL_THESIS_ROOT') or
        (project.parent if project.name=='magsim' and (project.parent/'calibration/pilot_20261003_n8/manifest.json').is_file() else project)).resolve()
    storage=Path(storage or os.environ.get('MAGSIM_WORKBENCH_DIR') or project/'data/workbench').resolve()
    calibration=CalibrationWorkbench(root,storage/'calibration')
    motor=MotorWorkbench(root,storage/'motor',project)
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
        return jsonify(motor.jobs())

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
        return jsonify(motor.export_summary(job_id))

    @bp.post('/api/workbench/motor/jobs')
    def prepare_motor():
        data=request.get_json()
        return jsonify(motor.prepare(data['materials'],data.get('cores',4),data.get('measurement_protocol','periodic_cycle3_v1')))

    @bp.get('/api/workbench/motor/jobs/<job_id>')
    def motor_state(job_id):
        return jsonify(motor.state(job_id))

    @bp.post('/api/workbench/motor/jobs/<job_id>/submit')
    def submit_motor(job_id):
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

    if os.environ.get('MAGSIM_CPU_ONLY') != '1' and ((storage/'motor/queue.lock').exists() or any(j['status'] in ('queued','starting','running') for j in motor.jobs())):
        motor.start_queue_monitor()
    return bp
