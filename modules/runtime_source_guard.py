"""Keep saved readers available, but do not start new work from stale code."""
import re
from flask import request,jsonify
from modules.system_runtime import source_manifest


EXACT_STARTS={'/api/run-script','/api/ml/train','/api/ml/paper-train','/api/pipeline/start',
              '/api/workbench/motor/queue/recover'}
START_PATHS=(r'/api/pipeline/[^/]+/(?:resume|rerun)',
    r'/api/workbench/calibrated-motor/(?:executions|native-imports)/[^/]+/start',
    r'/api/workbench/calibrated-motor/batches/[^/]+/(?:start|resume)',
    r'/api/workbench/motor/jobs/[^/]+/(?:submit|continue)',
    r'/api/workbench/optimization/plans/[^/]+/submit')


def starts_new_work(path):
    return path in EXACT_STARTS or any(re.fullmatch(pattern,path) for pattern in START_PATHS)


def runtime_view(snapshot):
    result=dict(snapshot)
    try:
        files,current=source_manifest(snapshot['project_path'])
        same=current==snapshot.get('source_fingerprint')
        result.update(current_source_fingerprint=current,current_source_file_count=len(files),
                      source_matches_startup=same,restart_required=not same)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result.update(current_source_fingerprint=None,source_matches_startup=False,
                      restart_required=True,source_read_error=str(exc))
    return result


def install_source_guard(app,snapshot):
    """No process management, queue recovery, reloading or artifact mutations."""
    key='magsim_source_guard'
    if key in app.extensions:return
    app.extensions[key]=dict(snapshot)

    @app.before_request
    def block_stale_native_starts():
        if request.method!='POST' or not starts_new_work(request.path):return None
        status=runtime_view(app.extensions[key])
        if status['restart_required']:
            return jsonify(error='当前服务与磁盘源码版本不一致或源码暂不可读；请重新运行启动器打开匹配版本。已有结果和活动任务保持，未下发新任务。',
                           code='source_version_changed',restart_required=True),409
        return None
