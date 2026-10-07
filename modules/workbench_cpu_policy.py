"""CPU-only local workbench mode; read routes and explicit analysis are allowed."""
import re
from flask import request,jsonify


def install_cpu_policy(app):
    @app.before_request
    def cpu_only_submissions():
        if request.method not in ('GET','HEAD','OPTIONS'):
            path=request.path
            safe=request.method=='POST' and (path in ('/api/workbench/calibrate','/api/workbench/motor/jobs','/api/workbench/motor/archive/export')
                or re.fullmatch(r'/api/workbench/calibrations/cal_[0-9a-f]{12}/predict(?:-excluded)?',path)
                or re.fullmatch(r'/api/workbench/motor/jobs/scan_[0-9a-f]{12}/export',path))
            if not safe:
                return jsonify(error='当前为 CPU 分析模式，原生求解和训练提交已暂停；恢复须用户明确授权'),423
