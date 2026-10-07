import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.motor_explicit_axes import same_vertices,candidates


class MotorAxisCandidates(unittest.TestCase):
    def test_actual_vertices_match_permutation_but_not_repeated_nearest_point(self):
        points=[[0,0,0],[.02,0,0],[.02,.001,0],[0,.001,0]]
        same_vertices(points[::-1],points)
        with self.assertRaises(ValueError):same_vertices([points[0]]*4,points)
        changed=np.array(points,float);changed[1,0]+=.001
        with self.assertRaises(ValueError):same_vertices(changed,points)

    def data(self):
        records=[];actual=[]
        for i in range(48):
            name='GOES_V3_'+str(i);origin=[.01,.0005,0.]
            records.append(dict(object=name,object_id=i,CS=name+'_CS',origin_m=origin,
                x_axis_absolute_m=[.011,.0005,0.],y_axis_absolute_m=[.01,.0015,0.],
                settings={'DrivenByXAxis':'false'}))
            actual.append(dict(object=name,native_vertices_m=[[0,0,0],[.02,0,0],[.02,.001,0],[0,.001,0]]))
        return records,dict(inserts=actual,native_model_units='mm',working_CS='Global')

    def test_candidate_bound_to_major_axis_without_claiming_measured_direction(self):
        records,geometry=self.data();result=candidates(records,geometry)
        self.assertEqual(len(result),48);self.assertEqual(records[0]['settings']['DrivenByXAxis'],'false')
        self.assertFalse(result[0]['measured_rolling_direction'])
        self.assertEqual(result[0]['object_CS']['x_axis_absolute_m'],[.001,0.,0.])
        self.assertEqual(result[0]['major_axis_binding']['RD_major_axis_unsigned_angle_deg'],0.)

    def test_missing_objects_wrong_global_frame_and_perpendicular_axis_rejected(self):
        records,geometry=self.data()
        with self.assertRaises(ValueError):candidates(records[:-1],geometry)
        geometry['working_CS']='Relative'
        with self.assertRaises(ValueError):candidates(records,geometry)
        geometry['working_CS']='Global';records[0]['x_axis_absolute_m']=[.01,.0015,0.];records[0]['y_axis_absolute_m']=[.009,.0005,0.]
        with self.assertRaises(ValueError):candidates(records,geometry)


if __name__=='__main__':unittest.main()
