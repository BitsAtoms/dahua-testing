"""Runs the editor's JavaScript geometry tests and checks that the editor and
the server agree on which rooms are valid."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.spaces import SpaceMapError, default_space_map, validate_space_map  # noqa: E402


NODE = shutil.which("node")
GEOMETRY = REPOSITORY_ROOT / "services" / "batcomputer-ui" / "web" / "editor" / "geometry.js"

# Each case is a list of rooms (outlines) drawn on one floor.
CASES = {
    "rectangle": [[(0, 0), (4, 0), (4, 3), (0, 3)]],
    "l_shape": [[(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)]],
    "u_shape": [[(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)]],
    "triangle": [[(0, 0), (4, 0), (0, 4)]],
    "diagonal": [[(0, 0), (4, 0), (4, 4), (1, 3)]],
    "straight_corner": [[(0, 0), (2, 0), (4, 0), (4, 4), (0, 4)]],
    "repeated": [[(0, 0), (4, 0), (4, 0), (4, 4), (0, 4)]],
    "bow_tie": [[(0, 0), (2, 0), (2, 4), (4, 4), (4, 2), (0, 2)]],
    "touching_walls": [[(0, 0), (6, 0), (6, 4), (4, 4), (4, 2), (2, 2), (2, 4), (6, 4), (6, 6), (0, 6)]],
    "outside": [[(40, 0), (49, 0), (49, 4), (40, 4)]],
    "neighbours": [[(0, 0), (4, 0), (4, 4), (0, 4)], [(4, 0), (8, 0), (8, 4), (4, 4)]],
    "overlap": [[(0, 0), (4, 0), (4, 4), (0, 4)], [(3, 3), (8, 3), (8, 8), (3, 8)]],
    "nested": [[(0, 0), (9, 0), (9, 9), (0, 9)], [(2, 2), (4, 2), (4, 4), (2, 4)]],
}

JS_CHECK = """
import { roomProblem } from %s;
const cases = JSON.parse(process.argv[1]);
const grid = { columns: 48, rows: 30 };
const answers = {};
for (const [name, outlines] of Object.entries(cases)) {
  const rooms = [];
  let problem = null;
  for (const outline of outlines) {
    const polygon = outline.map(([x, y]) => ({ x, y }));
    problem = problem || roomProblem(polygon, rooms, grid);
    rooms.push({ name: `sala ${rooms.length}`, polygon });
  }
  answers[name] = problem === null;
}
console.log(JSON.stringify(answers));
"""


def server_accepts(outlines: list[list[tuple[int, int]]]) -> bool:
    plan = default_space_map()
    plan["floors"][0]["rooms"] = [
        {"id": f"room_{index}", "name": f"Sala {index}",
         "polygon": [{"x": x, "y": y} for x, y in outline]}
        for index, outline in enumerate(outlines)
    ]
    try:
        validate_space_map(plan)
    except SpaceMapError:
        return False
    return True


@unittest.skipUnless(NODE, "node is not installed")
class EditorGeometryTests(unittest.TestCase):
    def test_javascript_geometry_tests_pass(self) -> None:
        result = subprocess.run(
            [NODE, "--test", str(Path(__file__).with_name("editor_geometry.test.mjs"))],
            cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_editor_and_server_agree_on_valid_rooms(self) -> None:
        script = JS_CHECK % json.dumps(GEOMETRY.as_uri())
        result = subprocess.run(
            [NODE, "--input-type=module", "-e", script, json.dumps(CASES)],
            cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        editor = json.loads(result.stdout)
        server = {name: server_accepts(outlines) for name, outlines in CASES.items()}
        self.assertEqual(editor, server)
        self.assertTrue(server["l_shape"] and server["neighbours"] and server["u_shape"])
        self.assertFalse(server["overlap"] or server["nested"] or server["touching_walls"])


if __name__ == "__main__":
    unittest.main()
