"""Real launcher checks resolve paths without starting Flask, browsers or solvers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT=Path(__file__).resolve().parents[1]


class LaunchConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='go steel launch ')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.env={**os.environ,'MAGSIM_AUTO_RESUME_QUEUE':'0','MAGSIM_CPU_ONLY':'0'}

    def config(self,args,cwd=None,env=None):
        result=subprocess.run(args,cwd=cwd or self.folder,env=env or self.env,
            capture_output=True,text=True,encoding='utf8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        return json.loads(result.stdout)

    def batch(self,path,extra=''):
        result=subprocess.run('call "'+str(path)+'" --check-config '+extra,
            shell=True,cwd=self.folder,env=self.env,capture_output=True,
            text=True,encoding='utf8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        return json.loads(result.stdout)

    def test_check_config_keeps_explicit_paths_modes_and_creates_no_records(self):
        root=self.folder/'requested resources';storage=self.folder/'requested records'
        result=self.config([sys.executable,'-X','utf8',str(PROJECT/'tools/start_workbench.py'),
            '--check-config','--open-browser','--cpu-only','--port','5432','--page','workbench',
            '--root',str(root),'--storage',str(storage)],
            env={**self.env,'GO_STEEL_THESIS_ROOT':str(self.folder/'ignored root'),
                 'MAGSIM_WORKBENCH_DIR':str(self.folder/'ignored records')})
        self.assertEqual(Path(result['root_path']),root)
        self.assertEqual(Path(result['storage_path']),storage)
        self.assertTrue(result['read_only']);self.assertFalse(result['application_started'])
        self.assertTrue(result['cpu_only']);self.assertFalse(result['auto_resume_queue'])
        self.assertEqual(result['requested_port'],5432);self.assertEqual(result['page'],'workbench')
        self.assertEqual(list(self.folder.iterdir()),[])

    @unittest.skipUnless(os.name=='nt','Windows batch entry')
    def test_nested_and_workspace_entries_agree_and_forward_overrides(self):
        nested=self.batch(PROJECT/'start_system.cmd')
        workspace=PROJECT.parent
        if ((workspace/'AGENTS.md').is_file() and
                (workspace/'calibration/pilot_20261003_n8/manifest.json').is_file() and
                (workspace/'start_system.cmd').is_file()):
            self.assertEqual(nested,self.batch(workspace/'start_system.cmd'))
            self.assertEqual(Path(nested['root_path']),workspace)
        else:
            self.assertEqual(Path(nested['root_path']),PROJECT)
            self.assertEqual(Path(nested['storage_path']),PROJECT/'data/system_workbench')
        root=self.folder/'override resources';storage=self.folder/'override records'
        result=self.batch(PROJECT/'start_system.cmd',
            '--root "'+str(root)+'" --storage "'+str(storage)+'" --port 5433 --page workbench --cpu-only')
        self.assertEqual(Path(result['root_path']),root)
        self.assertEqual(Path(result['storage_path']),storage)
        self.assertEqual(result['requested_port'],5433);self.assertEqual(result['page'],'workbench')
        self.assertTrue(result['cpu_only']);self.assertFalse(result['auto_resume_queue'])
        self.assertEqual(list(self.folder.iterdir()),[])

    @unittest.skipUnless(os.name=='nt','Windows batch entry')
    def test_standalone_public_style_entry_in_path_with_spaces_needs_no_workspace(self):
        project=self.folder/'standalone program'
        for name in ('start_system.cmd','tools/start_workbench.py','modules/system_runtime.py'):
            target=project/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(PROJECT/name,target)
        before={p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*') if p.is_file()}
        result=self.batch(project/'start_system.cmd')
        self.assertEqual(Path(result['project_path']),project)
        self.assertEqual(Path(result['root_path']),project)
        self.assertEqual(Path(result['storage_path']),project/'data/system_workbench')
        self.assertFalse((project/'data').exists())
        # Python may cache the copied stdlib-only module; no task or result files are created.
        after={p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
        self.assertEqual(before,after)


if __name__=='__main__':unittest.main()
