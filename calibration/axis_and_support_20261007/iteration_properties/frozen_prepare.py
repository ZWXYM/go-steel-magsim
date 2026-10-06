import json,sys,os,shutil
from pathlib import Path
root=Path('D:/Project/go-steel-thesis');sys.path.insert(0,str(root/'magsim'))
from modules.motor_workbench import write_json,license_config
from modules.maxwell_material_transport import digest
from modules.motor_queue import process_identity
source=root/'calibration/diagnostics/interface_controls_20261007/millimeter_v2/private_native/scalar_RD_full_boundary/control.aedt'
output=Path(__file__).resolve().parent
before=digest(source);project=output/'private_copy/control.aedt';project.parent.mkdir()
shutil.copy2(source,project)
write_json(output/'protocol.json',dict(maximum_new_solves=0,source_project_sha256=before,source_project=source.relative_to(root).as_posix(),producer_sha256=digest(Path(__file__)),maximum_native_sessions=1,scope='Prepare-only cloned configuration; no analyze call'))
config,_=license_config()
if config:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
from ansys.aedt.core import Desktop,Maxwell2d
import psutil
existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
desktop=None;owned=False;record={}
try:
 desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
 pid=desktop.aedt_process_id
 if pid in existing:raise ValueError('Dedicated session required')
 owned=True;write_json(output/'desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
 app=Maxwell2d(project=str(project),design='UniformFlux',version='2025.1',non_graphical=True,new_desktop=False,aedt_process_id=pid,close_on_exit=False)
 child=app.odesign.GetChildObject('Analysis').GetChildObject('ControlSetup')
 record['before']={p:child.GetPropValue(p) for p in child.GetPropNames()}
 child.SetPropValue('Use Nonlinear Iteration',True)
 record['after']={p:child.GetPropValue(p) for p in child.GetPropNames()}
 app.save_project()
 record['saved_project_sha256']=digest(project)
 record['native_validation']=app.validate_simple(str(output/'validation.log'))
 record['new_field_solves']=0
 record['native_messages']=list(desktop.odesktop.GetMessages(app.project_name,app.design_name,0))
finally:
 if desktop is not None and owned:desktop.release_desktop(close_projects=True,close_on_exit=True)
 if digest(source)!=before:raise ValueError('Source project changed')
 write_json(output/'prepared_properties.json',record)
