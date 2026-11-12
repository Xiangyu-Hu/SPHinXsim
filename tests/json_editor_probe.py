"""Exercise the shared Qt property editor in its own QApplication process."""
import json
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_API"] = "pyside6"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6 import QtWidgets
from sphinxsim.ui.json_editor import JsonPropertyEditor
from sphinxsim.ui.workbench.document import WorkbenchDocument

app = QtWidgets.QApplication([])
workspace = Path(sys.argv[1]).resolve()
workspace.mkdir(parents=True, exist_ok=True)
source = (ROOT / "tests/test_simulation/test_2d_simulation/data/column_collapse.json").read_text(encoding="utf-8")
payload = json.loads(source)
payload["gravity"] = [0.0, -9.8]
payload["fluid_boundary_conditions"] = []
source = json.dumps(payload, indent=2).replace('"gravity": [\n    0.0,\n    -9.8\n  ]', '"gravity": [0.0, -9.800e+00]')
source = source.replace("\n", "\r\n") + "\r\n"
case = workspace / "case.json"
case.write_bytes(source.encode("utf-8"))
document = WorkbenchDocument(case)
editor = JsonPropertyEditor()
editor.set_text(document.text, config_path=case, saved_text=document.saved_text)
editor.textChanged.connect(document.set_text)
assert editor.text() == source
assert not editor.dirty
assert editor.tree.headerItem().text(0) == "Setting"
assert editor.tree.headerItem().text(1) == "Value"

path = ("solver_parameters", "end_time")
original_end = editor._fields[path].text()
editor._fields[path].setText("0.08")
assert document.snapshot()["dirty"]
assert editor.validate_config().solver_parameters.end_time == 0.08
assert set(json.loads(editor.text())) == set(payload), "Default fields were inserted"
assert '"gravity": [0.0, -9.800e+00]' in editor.text(), "Unchanged numeric spelling or inline layout was lost"
assert "\r\n" in editor.text() and "\r\r\n" not in editor.text()
assert case.read_bytes().decode("utf-8") == source

editor._fields[path].setText("invalid")
try:
    editor.validate_config()
    raise AssertionError("Invalid numeric input was accepted")
except ValueError:
    pass
assert case.read_bytes().decode("utf-8") == source
editor.revert()
assert editor.validate_config().solver_parameters.end_time == 0.08
editor.filter.setText("end_time")
assert not editor._items[path].isHidden()
assert editor._items[("gravity",)].isHidden()
editor.filter.clear()
editor.tree.collapseAll()
reload_requests = []
editor.reloadRequested.connect(lambda: reload_requests.append(True))
editor.buttons["Reload"].click()
assert reload_requests == [True]

array = ("geometries", "shapes")
before = json.loads(editor.text())["geometries"]["shapes"]
editor.tree.setCurrentItem(editor._items[(*array, 0)])
editor.buttons["Duplicate"].click()
assert len(json.loads(editor.text())["geometries"]["shapes"]) == len(before) + 1
assert not editor._items[array].isExpanded(), "Collapsed tree state was lost"
editor.tree.setCurrentItem(editor._items[(*array, 0)])
editor.buttons["Move down"].click()
editor.tree.setCurrentItem(editor._items[(*array, 1)])
editor.buttons["Remove"].click()
assert json.loads(editor.text())["geometries"]["shapes"] == before

empty = ("fluid_boundary_conditions",)
editor.tree.setCurrentItem(editor._items[empty])
editor.buttons["Add"].click()
assert json.loads(editor.text())["fluid_boundary_conditions"][0]["type"] == "emitter"
editor.revert()
assert json.loads(editor.text())["fluid_boundary_conditions"] == []
editor.validate_config()
document.save()
editor.mark_saved()
assert not editor.dirty
assert case.read_bytes().decode("utf-8") == editor.text()
assert WorkbenchDocument(case).text == editor.text()

# A combo-box edit must publish to the same live draft as numeric inputs.
combo = editor._fields[("simulation_type",)]
assert isinstance(combo, QtWidgets.QComboBox)
combo.setCurrentText("fluid_dynamics")
assert json.loads(document.text)["simulation_type"] == "fluid_dynamics"
editor.revert()
assert json.loads(document.text)["simulation_type"] == "continuum_dynamics"
editor.close()

# The original shell Preview must host the same editor and retain its save path.
from types import SimpleNamespace
from sphinxsim.cli import _ShellPreviewRuntime
from sphinxsim.config.schemas import SimulationConfig
shell_case = workspace / "shell.json"
shell_case.write_bytes(source.encode("utf-8"))
shell_window = QtWidgets.QMainWindow()
runtime = _ShellPreviewRuntime()
runtime.plotter = SimpleNamespace(app_window=shell_window)
updates = []
runtime.show_or_update = lambda config, **kwargs: updates.append((config, kwargs)) or 0
assert runtime._install_json_editor(SimulationConfig.model_validate_json(source), config_path=shell_case, with_particles=False)
shared = runtime._json_editor["widget"]
assert isinstance(shared, JsonPropertyEditor)
shared._fields[path].setText("0.12")
shared.saveRequested.emit()
assert json.loads(shell_case.read_bytes())["solver_parameters"]["end_time"] == 0.12
assert '"gravity": [0.0, -9.800e+00]' in shell_case.read_bytes().decode("utf-8")
assert len(updates) == 1
assert updates[0][1]["resolved_config_path"] == shell_case
assert updates[0][1]["force"] is True
shell_window.close()
print("Property editor: typed edits, validation, arrays, undo, filtering, live document and format-safe save passed.")
