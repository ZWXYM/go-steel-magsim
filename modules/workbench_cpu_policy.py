"""CPU-only local workbench mode; read routes and explicit analysis are allowed."""
import re
from flask import request,jsonify


def install_cpu_policy(app):
    @app.before_request
    def cpu_only_submissions():
        if request.method not in ('GET','HEAD','OPTIONS'):
            path=request.path
            safe=request.method=='POST' and (path in ('/api/workbench/calibrated-motor/analysis-plans','/api/workbench/calibrated-motor/native-imports','/api/workbench/calibrated-motor','/api/workbench/calibrate','/api/workbench/motor/jobs','/api/workbench/motor/archive/export','/api/workbench/optimization/analyze','/api/workbench/optimization/plans','/api/workbench/waveforms/compare','/api/workbench/waveforms/export')
                or re.fullmatch(r'/api/workbench/calibrated-motor/analysis-plans/bhanalysis_[0-9a-f]{12}/prepare-model',path)
                or re.fullmatch(r'/api/workbench/optimization/plans/plan_[0-9a-f]{12}/refresh',path)
                or re.fullmatch(r'/api/workbench/calibrations/cal_[0-9a-f]{12}/predict(?:-excluded)?',path)
                or re.fullmatch(r'/api/workbench/motor/jobs/scan_[0-9a-f]{12}/export',path))
            if not safe:
                return jsonify(error='当前窗口为 CPU 分析模式；校准、准备和整理可用，求解与训练请从普通模式工作台提交'),423
