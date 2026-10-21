"""Browser entrypoints for source-bound calibration and isolated motor scans."""
import json
import io
import os
from pathlib import Path

from flask import Blueprint,jsonify,request,render_template,send_file
from modules.calibration_workbench import CalibrationWorkbench
from modules.calibration_beta_preview import CalibrationBetaPreview
from modules.motor_workbench import MotorWorkbench
from modules.workbench_cpu_policy import install_cpu_policy
from modules.system_runtime import runtime_identity
from modules.runtime_source_guard import runtime_view,install_source_guard
from modules.motor_optimization import MotorOptimization
from modules.motor_failure_diagnostics import diagnose_job,append_export_diagnostics,readable_motor_jobs
from modules.optimization_plans import OptimizationPlans
from modules.motor_waveforms import MotorWaveforms
from modules.material_library import MaterialLibrary
from modules.material_loss_reference import MaterialLossReference,reference_csv,reference_bundle,json_bytes as loss_json_bytes
from modules.material_input_view import MaterialInputView
from modules.calibrated_motor_preparation import CalibratedMotorPreparation
from modules.calibrated_motor_native import CalibratedMotorNative
from modules.calibrated_motor_analysis import CalibratedMotorAnalysis
from modules.calibrated_motor_execution import CalibratedMotorExecution
from modules.material_motor_workflow import MaterialMotorWorkflow
from modules.material_motor_chain import MaterialMotorChain
from modules.material_motor_batches import MaterialMotorBatches
from modules.material_batch_reports import report_bundle as material_batch_report_bundle
from modules.material_batch_reports import baseline_comparison, baseline_comparison_csv, baseline_report_bundle
from modules.native_pair_exports import NativePairExports
from modules.native_material_runs import NativeMaterialRuns

SYSTEM_GUIDE_DOCUMENTS=frozenset(('system_user_guide.md','custom_material_full_chain_20261020.md','batch_result_views_20261020.md'))


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
    loss_reference=MaterialLossReference(project)
    input_view=MaterialInputView(materials)
    calibrated_motor=CalibratedMotorPreparation(materials,motor,storage/'calibrated_motor')
    native_material=CalibratedMotorNative(calibrated_motor,motor,storage/'calibrated_motor_native')
    bh_analysis=CalibratedMotorAnalysis(native_material,waveforms,storage/'calibrated_motor_analysis')
    bh_execution=CalibratedMotorExecution(bh_analysis,motor,storage/'calibrated_motor_execution')
    material_workflow=MaterialMotorWorkflow(input_view,calibrated_motor,native_material,bh_analysis,bh_execution)
    material_chain=MaterialMotorChain(materials,calibrated_motor,native_material,bh_analysis,bh_execution)
    beta_preview=CalibrationBetaPreview(calibration)
    material_batches=MaterialMotorBatches(material_chain,storage/'calibrated_motor_batches')
    native_pairs=NativePairExports(root,storage/'native_pair_exports')
    native_runs=NativeMaterialRuns(calibration,motor,native_pairs,storage/'native_material_runs')
    identity=runtime_identity(project,root,storage)
    bp=Blueprint('workbench',__name__)
    if os.environ.get('MAGSIM_CPU_ONLY')=='1':
        bp.record_once(lambda state:install_cpu_policy(state.app))
    bp.record_once(lambda state:install_source_guard(state.app,identity))

    @bp.errorhandler(ValueError)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(FileNotFoundError)
    def bad_input(error):
        return jsonify(error=str(error)),400

    @bp.get('/workbench')
    def page():
        return render_template('workbench.html')

    @bp.get('/api/workbench/native-pair-sources')
    def native_pair_sources():
        return jsonify(native_pairs.catalog())

    @bp.get('/native-material-runs')
    def native_material_run_page():
        return render_template('native_material_runs.html')

    @bp.get('/api/workbench/native-material-runs')
    def native_material_run_records():
        return jsonify(native_runs.records())

    @bp.post('/api/workbench/native-material-runs')
    def native_material_run_prepare():
        return jsonify(native_runs.prepare(request.get_json()))

    @bp.get('/api/workbench/native-material-runs/<artifact>')
    def native_material_run_record(artifact):
        return jsonify(native_runs.get(artifact))

    @bp.post('/api/workbench/native-material-runs/<artifact>/start')
    def native_material_run_start(artifact):
        return jsonify(native_runs.start(artifact))

    @bp.post('/api/workbench/native-material-runs/<artifact>/pause')
    def native_material_run_pause(artifact):
        return jsonify(native_runs.pause(artifact))

    @bp.post('/api/workbench/native-material-runs/<artifact>/reconcile')
    def native_material_run_reconcile(artifact):
        return jsonify(native_runs.reconcile(artifact))

    @bp.get('/api/workbench/native-material-runs/<artifact>/bundle')
    def native_material_run_bundle(artifact):
        return send_file(native_runs.bundle(artifact),as_attachment=True,download_name=artifact+'.zip',mimetype='application/zip')

    @bp.get('/api/workbench/native-pairs')
    def native_pair_records():
        return jsonify(native_pairs.records())

    @bp.post('/api/workbench/native-pairs')
    def native_pair_prepare():
        return jsonify(native_pairs.prepare(request.get_json()))

    @bp.get('/api/workbench/native-pairs/<artifact>')
    def native_pair_record(artifact):
        return jsonify(native_pairs.get(artifact))

    @bp.get('/api/workbench/native-pairs/<artifact>/files/<name>')
    def native_pair_file(artifact,name):
        return send_file(native_pairs.download(artifact,name),as_attachment=True,download_name=name)

    @bp.get('/api/workbench/native-pairs/<artifact>/bundle')
    def native_pair_bundle(artifact):
        return send_file(native_pairs.bundle(artifact),as_attachment=True,download_name=artifact+'.zip',mimetype='application/zip')

    @bp.get('/system')
    def system_page():
        return render_template('system.html')

    @bp.get('/system/guide')
    @bp.get('/system/guide/<name>')
    def system_guide(name='system_user_guide.md'):
        from html import escape
        import markdown
        if name not in SYSTEM_GUIDE_DOCUMENTS:raise ValueError('该文档未列入系统操作说明')
        source=project/'docs'/name
        if not source.is_file():raise ValueError('当前程序副本缺少系统操作说明：'+name)
        text=source.read_text(encoding='utf-8-sig')
        # Local maintained instructions only; user-controlled file paths are not accepted.
        content=markdown.markdown(escape(text),extensions=['tables'])
        for target in SYSTEM_GUIDE_DOCUMENTS:
            content=content.replace('href="'+target+'"','href="/system/guide/'+target+'"')
        return render_template('system_guide.html',content=content,name=name)

    @bp.get('/system/guide/<name>/download')
    def system_guide_download(name):
        if name not in SYSTEM_GUIDE_DOCUMENTS:raise ValueError('该文档未列入系统操作说明')
        source=project/'docs'/name
        if not source.is_file():raise ValueError('当前程序副本缺少系统操作说明：'+name)
        return send_file(source,as_attachment=True,download_name=name,mimetype='text/markdown; charset=utf-8')

    @bp.get('/material-library')
    def material_page():
        return render_template('material_library.html')

    @bp.get('/material-loss-reference')
    def material_loss_reference_page():
        return render_template('material_loss_reference.html')

    @bp.get('/api/workbench/material-loss-reference')
    def material_loss_reference_data():
        output=request.args.get('format')
        if set(request.args)-{'format'} or any(len(request.args.getlist(k))!=1 for k in request.args) or output not in (None,'json','points_csv','estimates_csv','zip'):
            raise ValueError('损耗参考只接受单个format=json/points_csv/estimates_csv/zip')
        data=loss_reference.read()
        if output is None:return jsonify(data)
        if output=='zip':return send_file(reference_bundle(data),as_attachment=True,download_name='four_sample_loss_reference.zip',mimetype='application/zip')
        content=loss_json_bytes(data) if output=='json' else reference_csv(data,'points' if output=='points_csv' else 'estimates')
        return send_file(io.BytesIO(content),as_attachment=True,download_name='four_sample_loss_reference_'+output.replace('_csv','.csv')+('.json' if output=='json' else ''),mimetype='application/json' if output=='json' else 'text/csv')

    @bp.get('/material-motor')
    def material_workflow_page():
        return render_template('material_motor_workflow.html')

    @bp.get('/api/workbench/material-motor/workflows')
    def material_workflow_snapshot():
        return jsonify(material_workflow.snapshot())

    @bp.get('/api/workbench/material-motor/workflows/bundle')
    def material_workflow_bundle():
        return send_file(material_workflow.bundle(),as_attachment=True,
            download_name='material_motor_workflows.zip',mimetype='application/zip')

    @bp.post('/api/workbench/material-motor/prepare-or-reuse')
    def prepare_or_reuse_material_chain():
        return jsonify(material_chain.ensure(request.get_json()))

    @bp.get('/calibrated-motor/batches')
    def material_batch_page():
        return render_template('material_motor_batches.html')

    @bp.get('/api/workbench/calibrated-motor/batches')
    def material_batch_records():
        return jsonify(material_batches.records())

    @bp.post('/api/workbench/calibrated-motor/batches')
    def material_batch_prepare():
        return jsonify(material_batches.prepare(request.get_json()))

    @bp.get('/api/workbench/calibrated-motor/batches/<artifact>')
    def material_batch_record(artifact):
        return jsonify(material_batches.get(artifact))

    @bp.post('/api/workbench/calibrated-motor/batches/<artifact>/start')
    def material_batch_start(artifact):
        return jsonify(material_batches.start(artifact))

    @bp.post('/api/workbench/calibrated-motor/batches/<artifact>/resume')
    def material_batch_resume(artifact):
        return jsonify(material_batches.start(artifact,resume=True))

    @bp.post('/api/workbench/calibrated-motor/batches/<artifact>/pause')
    def material_batch_pause(artifact):
        return jsonify(material_batches.pause(artifact))

    @bp.get('/api/workbench/calibrated-motor/batches/<artifact>/bundle')
    def material_batch_bundle(artifact):
        return send_file(material_batch_report_bundle(material_batches,artifact),as_attachment=True,download_name=artifact+'.zip',mimetype='application/zip')

    @bp.get('/api/workbench/calibrated-motor/batches/<artifact>/comparison')
    def material_batch_comparison(artifact):
        baseline=request.args.get('baseline');output=request.args.get('format','json')
        if set(request.args)-{'baseline','format'} or any(len(request.args.getlist(k))!=1 for k in request.args) or output not in ('json','csv','zip'):
            raise ValueError('比较只接受单个baseline及json/csv/zip格式')
        if output=='zip':
            result=baseline_report_bundle(material_batches,artifact,baseline)
            return send_file(result,as_attachment=True,download_name=artifact+'_'+baseline+'_comparison.zip',mimetype='application/zip')
        result=baseline_comparison(material_batches,material_batches.get(artifact),baseline)
        if output=='csv':
            return send_file(io.BytesIO(baseline_comparison_csv(result)),as_attachment=True,download_name=artifact+'_'+baseline+'_comparison.csv',mimetype='text/csv')
        return jsonify(result)

    @bp.get('/api/workbench/material-inputs')
    def material_input_catalog():
        return jsonify(input_view.catalog())

    @bp.get('/api/workbench/material-inputs/<artifact>/<key>')
    def material_input_detail(artifact,key):
        return jsonify(input_view.detail(artifact,key))

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

    @bp.get('/api/workbench/calibrated-motor/executions/<artifact>/review')
    def bh_execution_review(artifact):
        from modules.motor_torque_review import review_execution
        return jsonify(review_execution(bh_execution,artifact))

    @bp.get('/api/workbench/calibrated-motor/executions/<artifact>/review.md')
    def bh_execution_review_markdown(artifact):
        from modules.motor_torque_review import review_execution, review_markdown
        import io
        report=review_execution(bh_execution,artifact)
        return send_file(io.BytesIO(review_markdown(report).encode('utf8')),as_attachment=True,
                         download_name=artifact+'_review.md',mimetype='text/markdown')

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
        return jsonify(runtime_view(identity))

    @bp.get('/api/workbench/samples')
    def samples():
        return jsonify(calibration.samples(include_quality=True))

    @bp.get('/api/workbench/resources')
    def resources():
        cpu_only=os.environ.get('MAGSIM_CPU_ONLY')=='1'
        status=runtime_view(identity)
        allowed=not cpu_only and not status['restart_required']
        return jsonify(cpu_only=cpu_only,native_submission_allowed=allowed,training_submission_allowed=allowed,
                       source_restart_required=status['restart_required'])

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

    @bp.get('/api/workbench/calibrations/<artifact>/beta-trial-preview')
    def beta_trial_preview(artifact):
        return jsonify(beta_preview.snapshot(artifact))

    @bp.get('/api/workbench/calibrations/<artifact>/beta-trial-preview/bundle')
    def beta_trial_preview_bundle(artifact):
        return send_file(beta_preview.bundle(artifact),as_attachment=True,
            download_name=artifact+'_beta_trial_preview.zip',mimetype='application/zip')

    @bp.get('/api/workbench/calibrations/<artifact>/beta-trial-preview/<kind>.csv')
    def beta_trial_preview_csv(artifact,kind):
        import io
        return send_file(io.BytesIO(beta_preview.csv(artifact,kind)),as_attachment=True,
            download_name=artifact+'_beta_trial_'+kind+'.csv',mimetype='text/csv')

    @bp.get('/api/workbench/calibrations/<artifact>/beta-trial-preview/download')
    def beta_trial_preview_download(artifact):
        import io
        data=beta_preview.snapshot(artifact)
        return send_file(io.BytesIO(json.dumps(data,ensure_ascii=False,indent=2).encode('utf8')),
            as_attachment=True,download_name=artifact+'_beta_trial_preview.json',mimetype='application/json')

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
        return jsonify([j if j['status']=='invalid' else diagnose_job(motor,j) for j in readable_motor_jobs(motor)])

    @bp.get('/api/workbench/motor/queue')
    def queue_state():
        invalid=[row['id'] for row in readable_motor_jobs(motor) if row['status']=='invalid']
        if invalid:raise ValueError('扫描状态不可读，队列归属尚不能确认：'+', '.join(invalid))
        try:return jsonify(motor.queue_status())
        except (ValueError,KeyError,TypeError,AttributeError,OSError) as error:raise ValueError('队列状态暂不可读：'+str(error)) from error

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
