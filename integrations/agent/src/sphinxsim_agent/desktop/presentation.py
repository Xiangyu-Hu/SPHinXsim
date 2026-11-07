"""Presentation adapter loaded only by the host-managed desktop.

Reuse native editor hooks/QActions; never interpret configuration or run a solver.
Unknown fields retain their upstream labels and all raw UserRole paths survive.
"""
from qtpy import QtCore, QtGui, QtWidgets

# Units copied from the native editor; values and enum tokens are never translated.
FIELDS = {
    "simulation_type": ("仿真类型", "Simulation type"),
    "simulation": ("仿真设置", "Simulation"), "domain": ("计算域", "Domain"),
    "global_resolution": ("粒子分辨率", "Particle resolution"),
    "particle_spacing": ("粒子间距 [m]", "Particle spacing [m]"),
    "lower_bound": ("下界 [m]", "Lower bound [m]"), "upper_bound": ("上界 [m]", "Upper bound [m]"),
    "half_size": ("半尺寸 [m]", "Half size [m]"), "translation": ("平移 [m]", "Translation [m]"),
    "center": ("中心 [m]", "Center [m]"), "radius": ("半径 [m]", "Radius [m]"),
    "density": ("密度 [kg/m³]", "Density [kg/m³]"), "gravity": ("重力 [m/s²]", "Gravity [m/s²]"),
    "sound_speed": ("声速 [m/s]", "Sound speed [m/s]"), "viscosity": ("动力黏度 [Pa·s]", "Viscosity [Pa·s]"),
    "youngs_modulus": ("杨氏模量 [Pa]", "Young's modulus [Pa]"), "poisson_ratio": ("泊松比", "Poisson ratio"),
    "friction_angle": ("摩擦角 [rad]", "Friction angle [rad]"), "dilatancy_angle": ("剪胀角 [rad]", "Dilatancy angle [rad]"),
    "cohesion": ("黏聚力 [Pa]", "Cohesion [Pa]"), "yield_stress": ("屈服应力 [Pa]", "Yield stress [Pa]"),
    "end_time": ("结束时间 [s]", "End time [s]"), "output_interval": ("输出间隔 [s]", "Output interval [s]"),
    "inflow_speed": ("入口速度 [m/s]", "Inflow speed [m/s]"),
    "fluid_bodies": ("流体物体", "Fluid bodies"), "solid_bodies": ("固体物体", "Solid bodies"),
    "continuum_bodies": ("连续体物体", "Continuum bodies"), "geometry": ("几何", "Geometry"),
    "material": ("材料", "Material"), "boundary_conditions": ("边界条件", "Boundary conditions"),
}
BUTTONS = {
    "Expand": ("expand", "展开", "Expand"), "Collapse": ("collapse", "折叠", "Collapse"),
    "Revert": ("revert", "恢复", "Revert"), "Reload": ("reload", "重新载入", "Reload"),
    "Add": ("add", "添加", "Add"), "Duplicate": ("duplicate", "复制", "Duplicate"),
    "Remove": ("remove", "删除", "Remove"), "Move up": ("move-up", "上移", "Move up"),
    "Move down": ("move-down", "下移", "Move down"),
}


class Presentation(QtCore.QObject):
    def __init__(self, runtime, simulation_actions):
        self.window = runtime.plotter.app_window
        super().__init__(self.window)
        self.runtime = runtime
        self.editor = runtime._json_editor
        self.status_text = {}
        required = ("dock", "tree", "filter", "shortcut", "undo_shortcut", "refresh", "is_dirty")
        if not self.editor or any(key not in self.editor for key in required):
            raise ValueError("Native editor presentation hooks are incompatible")
        self.locale, self.theme = "en", "dark"
        self.settings = QtCore.QSettings("/work/control/presentation.ini", QtCore.QSettings.Format.IniFormat)
        self.dock = self.editor["dock"]
        self.dock.setMinimumWidth(260)
        self.dock.installEventFilter(self)
        self.buttons = {}
        for button in self.dock.findChildren(QtWidgets.QToolButton):
            if button.text() in BUTTONS:
                info = BUTTONS[button.text()]
                button.setObjectName("sphinxsim-editor-" + info[0])
                self.buttons[info[0]] = (button, info)
        if any(key not in self.buttons for key in ("reload", "revert", "expand", "collapse")):
            raise ValueError("Native editor action hooks are incompatible")
        self.labels = []
        menu_bar = self.window.menuBar()
        original = list(menu_bar.actions())
        for action in original:
            menu_bar.removeAction(action)  # Keep original QAction owners/callbacks alive.
        def menu(key, zh, en):
            value = menu_bar.addMenu(en)
            value.setObjectName("sphinxsim-menu-" + key)
            self.labels.append((value, zh, en))
            return value
        def action(menu, key, zh, en, callback):
            value = menu.addAction(en)
            value.setObjectName("sphinxsim-action-" + key)
            value.triggered.connect(lambda _checked=False: callback())
            self.labels.append((value, zh, en))
            return value
        files = menu("file", "文件", "File")
        action(files, "save", "保存配置", "Save configuration", self.editor["shortcut"].activated.emit)
        action(files, "reload", "重新载入", "Reload", self.buttons["reload"][0].click)
        action(files, "screenshot", "保存窗口截图…", "Save window screenshot…", self.screenshot)
        edit = menu("edit", "编辑", "Edit")
        action(edit, "undo", "撤销", "Undo", self.editor["undo_shortcut"].activated.emit)
        for key in ("revert", "add", "duplicate", "remove", "move-up", "move-down"):
            if key in self.buttons:
                button, info = self.buttons[key]
                value = action(edit, key, info[1], info[2], button.click)
                # The original buttons remain the authority for structural action eligibility.
                edit.aboutToShow.connect(lambda b=button, a=value: a.setEnabled(b.isEnabled()))
        view = menu("view", "视图", "View")
        view.addAction(self.dock.toggleViewAction())
        action(view, "fit", "适应模型", "Fit model", runtime.plotter.reset_camera)
        for key in ("expand", "collapse"):
            button, info = self.buttons[key]
            action(view, key, info[1], info[2], button.click)
        advanced = view.addMenu("Native view tools")
        self.labels.append((advanced, "原生视图工具", "Native view tools"))
        for original_action in original:
            advanced.addAction(original_action)
        simulation = menu("simulation", "仿真", "Simulation")
        for key, zh, en, existing in simulation_actions:
            existing.setObjectName("sphinxsim-action-" + key)
            self.labels.append((existing, zh, en))
            simulation.addAction(existing)  # Exactly the durable queue intent QAction.
        help_menu = menu("help", "帮助", "Help")
        action(help_menu, "help", "快捷键与版本", "Shortcuts and version", self.help)
        tree = self.editor["tree"]
        self.relabel_timer = QtCore.QTimer(self)
        self.relabel_timer.setSingleShot(True)
        self.relabel_timer.timeout.connect(self.relabel)
        tree.model().rowsInserted.connect(lambda *_: self.relabel_timer.start(0))
        tree.model().modelReset.connect(lambda *_: self.relabel_timer.start(0))
        old_refresh = self.editor["refresh"]
        def refresh(*args, **kwargs):
            query = self.editor["filter"].text()
            expanded = self.expanded_paths()
            result = old_refresh(*args, **kwargs)
            if result:
                self.relabel()
                self.restore_expanded(expanded)
                self.editor["filter"].setText(query)
            return result
        self.editor["refresh"] = refresh
        self.write_timer = QtCore.QTimer(self)
        self.write_timer.setSingleShot(True)
        self.write_timer.timeout.connect(self.persist)
        for signal in (tree.itemExpanded, tree.itemCollapsed, self.editor["filter"].textChanged, self.dock.visibilityChanged):
            signal.connect(lambda *_: self.write_timer.start(500))
        QtCore.QTimer.singleShot(200, self.restore)
        self.apply("en", "dark")

    def items(self):
        iterator = QtWidgets.QTreeWidgetItemIterator(self.editor["tree"])
        while iterator.value() is not None:
            yield iterator.value()
            iterator += 1

    def expanded_paths(self):
        return [item.data(0, QtCore.Qt.ItemDataRole.UserRole) for item in self.items() if item.isExpanded()]

    def restore_expanded(self, paths):
        for item in self.items():
            item.setExpanded(item.data(0, QtCore.Qt.ItemDataRole.UserRole) in paths)

    def relabel(self):
        tree = self.editor["tree"]
        blocker = QtCore.QSignalBlocker(tree)
        tree.setHeaderLabels(["参数", "值"] if self.locale == "zh" else ["Setting", "Value"])
        for item in self.items():
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if not isinstance(path, str):
                continue
            key = path.split(".")[-1]
            if key in FIELDS:
                item.setText(0, FIELDS[key][0 if self.locale == "zh" else 1])
            item.setToolTip(0, path)
        del blocker

    def apply(self, locale, theme):
        self.locale = "zh" if locale.startswith("zh") else "en"
        self.theme = theme
        zh = self.locale == "zh"
        for obj, chinese, english in self.labels:
            if isinstance(obj, QtWidgets.QMenu):
                obj.setTitle(chinese if zh else english)
            else:
                obj.setText(chinese if zh else english)
        self.dock.setWindowTitle("实验配置" if zh else "Experiment configuration")
        self.dock.toggleViewAction().setText("属性面板" if zh else "Properties")
        self.editor["filter"].setPlaceholderText("搜索参数…" if zh else "Search settings…")
        for button, info in self.buttons.values():
            button.setText(info[1 if zh else 2])
        self.relabel()
        self.editor["status"].setStyleSheet("")
        dark = theme == "dark"
        bg, surface, fg, muted, border, selected = (("#202226", "#282b30", "#e9eaed", "#acb0b8", "#3c4048", "#365b80") if dark else ("#f7f8fa", "#ffffff", "#20242a", "#606975", "#dce0e6", "#dceafa"))
        palette = QtGui.QPalette()
        for role, color in (("Window", bg), ("WindowText", fg), ("Base", surface), ("AlternateBase", bg), ("Text", fg), ("Button", surface), ("ButtonText", fg), ("Highlight", selected), ("HighlightedText", fg), ("ToolTipBase", surface), ("ToolTipText", fg)):
            palette.setColor(getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(color))
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QtGui.QColor(muted))
        self.window.setPalette(palette)
        # Remove upstream hardcoded dark chrome, without touching editor values/signals.
        for widget in [self.dock, *self.dock.findChildren(QtWidgets.QWidget)]:
            if widget.objectName() in ("sphinxsim-json-editor", "sphinxsim-json-editor-content", "sphinxsim-json-editor-tree", "sphinxsim-json-editor-filter"):
                widget.setStyleSheet("")
        for combo in self.dock.findChildren(QtWidgets.QComboBox):
            combo.view().setStyleSheet("")
        self.window.setStyleSheet(f"""
            QMainWindow, QMenuBar, QDockWidget {{ background: {bg}; color: {fg}; }}
            QMenu, QTreeWidget, QLineEdit, QComboBox, QToolButton {{ background: {surface}; color: {fg}; }}
            QTreeWidget {{ border: 0; alternate-background-color: {bg}; }}
            QTreeWidget::item {{ padding: 5px 3px; min-height: 24px; }}
            QTreeWidget::item:selected, QMenu::item:selected {{ background: {selected}; color: {fg}; }}
            QLineEdit, QComboBox, QToolButton {{ border: 1px solid {border}; border-radius: 4px; padding: 5px; }}
            QMenu::item {{ padding: 7px 28px; }}
            QHeaderView::section {{ background: {bg}; color: {muted}; padding: 7px; border: 0; }}
            QDockWidget::title {{ padding: 8px; }}
            QToolBar {{ background: {bg}; border: 0; spacing: 8px; padding: 4px; }}
            QLabel {{ color: {fg}; }}
        """)

    def translate_status(self, label):
        # Keep unknown native diagnostics verbatim, and never change config values.
        current = label.text()
        previous = self.status_text.get(label)
        raw = previous[0] if previous and current == previous[1] else current
        translations = {
            "queued": "等待计算资源", "preparing": "正在准备", "generating_particles": "正在生成粒子",
            "relaxing": "正在松弛粒子", "waiting": "粒子就绪，等待开始", "running": "正在计算",
            "postprocessing": "正在校验结果", "succeeded": "计算已完成", "failed": "计算失败",
            "canceling": "正在取消", "canceled": "已取消", "timed_out": "运行超时", "preview_expired": "粒子实例已释放",
            "Geometry is prepared asynchronously. Save with Ctrl+S.": "正在构建几何。Ctrl+S 保存配置。",
            "Waiting for the control plane…": "正在提交请求…",
            "Request saved; waiting for the control plane…": "请求已保存，等待调度…",
            "Geometry ready. Save with Ctrl+S, then generate particles.": "几何已就绪。Ctrl+S 保存后可生成粒子。",
            "Loaded configuration. Press Ctrl+S to save changes.": "配置已载入，Ctrl+S 保存修改。",
            "Unsaved changes. Press Ctrl+S to validate, save, and refresh.": "有未保存修改。Ctrl+S 校验、保存并刷新。",
            "Reloaded the saved configuration from disk.": "已重新载入保存的配置。",
            "Saved and rebuilding preview geometry…": "配置已保存，正在重建几何…",
        }
        displayed = raw
        if self.locale == "zh":
            prefix, separator, suffix = raw.partition(" • ")
            displayed = translations.get(raw, translations.get(prefix, prefix) + separator + suffix)
        if current != displayed:
            label.setText(displayed)
        self.status_text[label] = (raw, displayed)

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.Resize and hasattr(self, "write_timer"):
            self.write_timer.start(500)
        return False

    def persist(self):
        self.settings.setValue("width", self.dock.width())
        self.settings.setValue("visible", self.dock.isVisible())
        self.settings.setValue("query", self.editor["filter"].text())
        self.settings.setValue("expanded", self.expanded_paths())
        self.settings.sync()

    def restore(self):
        self.window.resizeDocks([self.dock], [int(self.settings.value("width", 320))], QtCore.Qt.Orientation.Horizontal)
        self.dock.setVisible(self.settings.value("visible", True, type=bool) and self.window.width() >= 1100)
        self.restore_expanded(self.settings.value("expanded", self.expanded_paths()))
        self.editor["filter"].setText(str(self.settings.value("query", "")))

    def screenshot(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self.window, "PNG", "/work/project/preview.png", "PNG (*.png)")
        if not path:
            return
        pos = self.window.mapToGlobal(QtCore.QPoint(0, 0))
        pixels = self.window.screen().grabWindow(0, pos.x(), pos.y(), self.window.width(), self.window.height())
        if not pixels.save(path, "PNG"):
            QtWidgets.QMessageBox.warning(self.window, "PNG", "Unable to save screenshot")

    def help(self):
        import importlib.metadata
        try:
            version = importlib.metadata.version("sphinxsim")
        except importlib.metadata.PackageNotFoundError:
            version = "source"
        text = ("保存 Ctrl+S · 撤销 Ctrl+Z\n鼠标旋转、平移和缩放沿用原生视图。\n仿真菜单通过执行后端运行；关闭窗口不会取消独立求解。" if self.locale == "zh" else "Save Ctrl+S · Undo Ctrl+Z\nUse native mouse rotation, pan and zoom.\nSimulation actions use the execution backend; closing this view does not cancel the solver.")
        QtWidgets.QMessageBox.information(self.window, "SPHinXsim " + version, text + "\nhttps://xiangyu-hu.github.io/SPHinXsim/visualization/")
