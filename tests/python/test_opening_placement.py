"""Opening placement uses local physical geometry below an unrelated protrusion."""
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from build123d import Box, Pos

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/text-a3d'))
import qa_check
from color import qa_check as color_qa
from geometry_binding import shape_to_mesh


class LocalOpeningTests(unittest.TestCase):
    def observation(self, helpers, root, *, closed=False, face='right', tamper=False):
        base = Box(20, 20, 20) + Pos(14, 0, 7)*Box(12, 10, 6)
        tool = Pos(6 if closed else 9, 0, -4)*Box(4 if closed else 6, 4, 4)
        shape = base-tool
        mesh = shape_to_mesh(shape, "local opening test")
        transform = np.array([[0,0,1,25],[1,0,0,20],[0,1,0,10],[0,0,0,1]], dtype=float)
        mesh.apply_transform(transform)
        path = root/'owner.stl'
        mesh.export(path)
        digest = sha256(path.read_bytes()).hexdigest()
        bbox = shape.bounding_box()
        tool_box = tool.bounding_box()
        report = {
            'part':'owner', 'parts':{'owner':{'semantic':{'boundsMm':{
                'min':list(bbox.min), 'max':list(bbox.max), 'size':list(bbox.size)}}}},
            'features':{}, 'events':[{'id':'opening','kind':'cut','part':'owner','tool':{'bbox_mm':{
                'min':list(tool_box.min), 'max':list(tool_box.max), 'size':list(tool_box.size)}}}],
            'artifacts':{'stl:owner':{'path':str(path),'sha256':digest,'coordinateFrame':'part-print'}},
            'coordinateFrames':{'part-print':{'partTransforms':{'owner':transform.tolist()}}},
        }
        intent = {'features':[{'id':'opening','kind':'hole','face':face,'edge_crossing':'forbidden'}]}
        if tamper:
            path.write_bytes(path.read_bytes()+b'tampered')
        return helpers.semantic_placement_observation(intent, report)

    def test_recessed_exterior_opening_passes_but_internal_or_wrong_face_cuts_fail(self):
        for helpers in (qa_check, color_qa):
            with self.subTest(backend=helpers.__name__), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                good=self.observation(helpers,root)
                self.assertEqual(good['offenders'],[])
                self.assertEqual(good['observations'][0]['records'][0]['local_opening']['clear_sample_count'],9)
                self.assertTrue(self.observation(helpers,root,closed=True)['offenders'])
                self.assertTrue(self.observation(helpers,root,face='left')['offenders'])

    def test_unbound_mesh_cannot_supply_passing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'hash differs'):
                self.observation(qa_check,Path(directory),tamper=True)


if __name__=='__main__':
    unittest.main()
