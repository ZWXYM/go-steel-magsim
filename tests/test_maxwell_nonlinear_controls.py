import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_nonlinear_controls import frame,object_CS_hypotheses,geometry_metrics,make_protocol,payload,evaluate,length_m,saved_insert_geometry
from unittest.mock import patch
from unittest.mock import Mock
import json
from tools.run_nonlinear_field_probe import capture_messages
from test_maxwell_material_transport import fixture
from modules.maxwell_material_transport import load_contract
import tempfile

def CS():
    return dict(object='Fixture',CS='Fixture_CS',origin_m=[-.0314,.00135,0.],x_axis_absolute_m=[-.0324,.00135,0.],y_axis_absolute_m=[-.0314,.00035,0.])

class NonlinearControls(unittest.TestCase):
    def test_CS_point_and_vector_hypotheses_are_distinct_orthogonal(self):
        for kind in ('absolute_point','direction_vector'):
            R=frame(CS(),kind);np.testing.assert_allclose(R.T@R,np.eye(3),atol=1e-12)
        values=object_CS_hypotheses(CS(),.025)
        self.assertGreater(abs(values['direction_vector'][1]),1.)
        self.assertLess(abs(values['absolute_point'][1]),1e-10)

    def protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=load_contract(fixture(tmp))
            source['curves']['RD']={'H':[0.,10.,20.,100.],'B':[0.,.5,1.5,1.9]}
            return make_protocol(source,CS())

    def test_six_cases_freeze_scalar_tensor_smoothing_mesh_and_flux(self):
        protocol=self.protocol();self.assertEqual(len(protocol['cases']),6)
        self.assertFalse(protocol['retry_allowed'])
        self.assertEqual(protocol['cases'][2]['target_B_T'],protocol['cases'][3]['target_B_T'])
        self.assertEqual(protocol['cases'][3]['maximum_mesh_length_m'],.001)
        arguments=payload(protocol['cases'][0]);permeability=next(a for a in arguments if isinstance(a,list) and a[0]=='NAME:permeability')
        self.assertIn('nonlinear',permeability)
        self.assertFalse(any(isinstance(v,list) and v[0]=='NAME:component1' for v in permeability))

    def test_smoothing_never_excuses_source_knot_bias(self):
        case=self.protocol()['cases'][1]
        B,H=np.zeros((9,3)),np.zeros((9,3));B[:,0]=case['target_B_T'];H[:,0]=26
        result=evaluate(case,B,H,{'adaptive_converged':True})
        self.assertFalse(result['passed']);self.assertFalse(result['checks']['H_source_knot'])

    def test_native_nonconvergence_blocks_otherwise_exact_scalar_fields(self):
        case=self.protocol()['cases'][0]
        B,H=np.zeros((9,3)),np.zeros((9,3));B[:,0]=case['target_B_T'];H[:,0]=20
        self.assertFalse(evaluate(case,B,H,{'adaptive_converged':False})['passed'])

    def test_translated_CS_identification_is_analytic_and_does_not_certify_material(self):
        case=self.protocol()['cases'][-1]
        B=np.tile([case['target_B_T'],0,0],(9,1));H=np.tile(case['expected_H_hypotheses_A_per_m']['absolute_point'],(9,1))
        result=evaluate(case,B,H,{'adaptive_converged':True})
        self.assertTrue(result['passed']);self.assertEqual(result['CS_interpretation'],'absolute_point')
        self.assertFalse(result['calibrated_RD_TD_field_verified'])

    def test_geometry_axes_and_literal_units_reject_ambiguous_inputs(self):
        record=CS();points=[[-.04,0,0],[-.02,0,0],[-.02,.001,0],[-.04,.001,0]]
        self.assertLess(geometry_metrics(record,points,'absolute_point')['RD_major_axis_unsigned_angle_deg'],1e-6)
        self.assertEqual(length_m('1mm'),.001)
        with self.assertRaises(ValueError):length_m('slot_width')
        with self.assertRaises(ValueError):geometry_metrics(record,points[:2],'absolute_point')

    def test_rotated_duplicate_requires_native_vertices_without_inventing_a_polyline(self):
        text="""$begin 'GeometryPart'
$begin 'Attributes'
Name='GOES_V3_01_1'
$end 'Attributes'
$begin 'Operation'
OperationType='DuplicateBodyAroundAxis'
$end 'Operation'
$begin 'ObjectCSParameters'
$begin 'Origin'
PositionType='AbsolutePosition'
IsAttachedToEntity=false
XPosition='-31.4mm'
YPosition='1.35mm'
ZPosition='0mm'
$end 'Origin'
$begin 'xAxis'
DirectionType='AbsoluteDirection'
xDirection='-32.4mm'
yDirection='1.35mm'
zDirection='0mm'
$end 'xAxis'
$begin 'yAxis'
DirectionType='AbsoluteDirection'
xDirection='-31.4mm'
yDirection='0.35mm'
zDirection='0mm'
$end 'yAxis'
ReverseXAxis=false
ReverseYAxis=false
$end 'ObjectCSParameters'
$end 'GeometryPart'"""
        audit=dict(local_CS_verified=True,inserts=[dict(name='GOES_V3_01_1',object_id=1,coordinate_system_name='CS',coordinate_system_id=2)])
        with patch('modules.maxwell_nonlinear_controls.inspect_model',return_value=audit):
            record=saved_insert_geometry(text)[0]
            self.assertIsNone(record['polyline_points_m'])
            self.assertFalse(record['current_global_vertices_verified'])
            with self.assertRaisesRegex(ValueError,'history'):
                saved_insert_geometry(text.replace('DuplicateBodyAroundAxis','UnknownTransform'))

    def test_native_error_messages_are_saved_before_session_release(self):
        desktop=Mock();desktop.odesktop.GetMessages.return_value=['[error] Native solver fixture failure']
        app=Mock(project_name='Fixture',design_name='Coupon')
        with tempfile.TemporaryDirectory() as tmp:
            capture_messages(desktop,app,Path(tmp))
            self.assertIn('fixture failure',json.loads((Path(tmp)/'native_messages.json').read_text())[0])
        desktop.odesktop.GetMessages.assert_called_once_with('Fixture','Coupon',0)

if __name__=='__main__':unittest.main()
