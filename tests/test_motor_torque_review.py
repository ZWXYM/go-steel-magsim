"""CPU checks of anomaly source binding, missing profiles and immutable results."""
import json
import os
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from flask import Flask
from modules.motor_torque_review import profile_rows,review_execution,review_markdown
from modules.material_library import sha
from modules.workbench_routes import create_workbench


def native_profile(times=(0,.001,.002)):
    groups=[]
    for i,t in enumerate(times):
        groups.append("$begin 'ProfileGroup'\nName='Time - "+str(t)+"s'\n"
                      "ProfileItem('Solve', 'Final Residual', "+str(i/10)+", 'Calls', "+str(40+i)+")\n$end 'ProfileGroup'\n")
    return "$begin 'Profile'\n"+''.join(groups)+"$end 'Profile'\n"


class TorqueReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'bhexec_aaaaaaaaaaaa'
        project=self.folder/'case/motor.aedt';project.parent.mkdir(parents=True)
        project.write_text("$begin 'Setup1'\nStopTime='0.002s'\nTimeStep='0.001s'\nUseNonLinearIterNum=false\nNonlinearSolverResidual='0.0001'\n$end 'Setup1'\n$begin 'WB_BH_Torque'\nExpr='-Moving1.Torque'\n$end 'WB_BH_Torque'\n",encoding='utf8')
        self.profile=project.parent/'motor.aedtresults/design.results/solution.profile';self.profile.parent.mkdir(parents=True)
        self.profile.write_text(native_profile(),encoding='utf8')
        self.csv=project.parent/'reports/Torque Plots.csv';self.csv.parent.mkdir();self.csv.write_bytes(b'Time [ms],-Moving1.Torque [Nm]\r\n0,1\r\n1,-2\r\n2,3\r\n')
        m=dict(time_ms=[0,1,2],torque_Nm=[1,-2,3],selected_window_ms=[0,2],torque_csv_sha256=sha(self.csv.read_bytes()),
               T_avg_Nm=2/3,T_min_Nm=-2,T_max_Nm=3,K_T_ripple_pct=750,torque_numerical_acceptance=False)
        self.saved=dict(material_name='WB_fixture',model_version='fixture_v1',measurement_protocol='v8_2_cycle1',
                        metrics=m,solved_project_sha256=sha(project.read_bytes()))
        (self.folder/'result.json').write_text(json.dumps(self.saved),encoding='utf8')
        self.manager=SimpleNamespace(get=lambda ident:dict(result_available=True,result=self.saved),path=lambda ident:self.folder)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_anomaly_retains_neighbor_points_and_reads_native_fields_without_certification(self):
        before=self.snapshot();r=review_execution(self.manager,'bhexec_aaaaaaaaaaaa');p=r['nonpositive_points'][0]
        self.assertEqual((p['time_ms'],p['torque_Nm']),(1,-2));self.assertEqual(p['previous']['torque_Nm'],1)
        self.assertEqual(p['next']['torque_Nm'],3);self.assertEqual(p['native_profile_row']['matrix_calls'],41)
        self.assertEqual(p['native_profile_row']['final_residual_raw'],.1);self.assertFalse(r['cause_identified'])
        self.assertFalse(r['nonlinear_convergence_certified']);self.assertEqual(r['new_native_solves'],0)
        self.assertIn('-Moving1.Torque',r['saved_report_expressions']);self.assertIsNone(r['saved_setup']['MaxIterNum'])
        self.assertEqual(before,self.snapshot());self.assertIn('-2',review_markdown(r));self.assertIn('不等同',review_markdown(r))

    def test_missing_or_multiple_matching_profiles_never_choose_arbitrary_residual(self):
        self.profile.rename(self.profile.with_suffix('.kept'))
        r=review_execution(self.manager,'x');self.assertIsNone(r['nonpositive_points'][0]['native_profile_row']);self.assertTrue(r['warnings'])
        self.profile.write_text(native_profile());self.profile.with_name('second.profile').write_text(native_profile())
        r=review_execution(self.manager,'x');self.assertIsNone(r['nonpositive_points'][0]['native_profile_row']);self.assertEqual(len(r['profiles']),2)

    def test_mismatched_time_grid_is_visible_without_attaching_residual(self):
        self.profile.write_text(native_profile((0,.002,.004)))
        r=review_execution(self.manager,'x');self.assertFalse(r['profiles'][0]['time_grid_matches'])
        self.assertIsNone(r['nonpositive_points'][0]['native_profile_row'])

    def test_profile_parse_failure_is_not_invented_or_used_to_drop_original_negative_point(self):
        self.profile.write_text("$begin 'ProfileGroup'\nName='Time - 0s'\n")
        r=review_execution(self.manager,'x');self.assertTrue(r['warnings']);self.assertEqual(r['nonpositive_points'][0]['torque_Nm'],-2)

    def test_duplicate_native_times_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'重复'):profile_rows(native_profile((0,0,.001)))

    def test_no_actual_result_and_changed_project_or_csv_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'保存完成'):
            review_execution(SimpleNamespace(get=lambda ident:dict(result_available=False)),'x')
        self.csv.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'来源在读取期间改变'):review_execution(self.manager,'x')
        (self.folder/'case/motor.aedt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'工程在检查前改变'):review_execution(self.manager,'x')

    def test_outside_selected_window_and_zero_preserve_original_acceptance(self):
        m=self.saved['metrics'];m.update(selected_window_ms=[1.5,2],torque_numerical_acceptance=True)
        r=review_execution(self.manager,'x');self.assertFalse(r['nonpositive_points'][0]['within_metric_window'])
        self.assertTrue(r['torque_numerical_acceptance']);self.assertEqual(r['findings'],[])
        m['torque_Nm'][1]=0;r=review_execution(self.manager,'x');self.assertEqual(r['nonpositive_points'][0]['torque_Nm'],0)

    def test_get_routes_work_in_cpu_mode_and_never_dispatch_native(self):
        project=Path(__file__).resolve().parents[1];app=Flask('review_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(project,self.root/'storage',self.root))
        url='/api/workbench/calibrated-motor/executions/bhexec_aaaaaaaaaaaa'
        report=review_execution(self.manager,'bhexec_aaaaaaaaaaaa')
        with patch('modules.motor_torque_review.review_execution',return_value=report) as reader:
            c=app.test_client();a=c.get(url+'/review');b=c.get(url+'/review.md')
            self.assertEqual(a.status_code,200);self.assertEqual(b.status_code,200)
            self.assertFalse(a.json['cause_identified']);self.assertIn('attachment',b.headers['Content-Disposition'])
            self.assertEqual(reader.call_count,2);self.assertEqual(c.post(url+'/start',json={}).status_code,423)


if __name__=='__main__':unittest.main()
