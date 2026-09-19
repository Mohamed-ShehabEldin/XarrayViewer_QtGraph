# xr_vis.py
from pathlib import Path
from importlib.resources import files
import json
import traceback

import numpy as np
import pyqtgraph as pg
import xarray as xr
from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMainWindow,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from scipy.interpolate import RegularGridInterpolator
from scipy.io import loadmat

try:
    from .draggable_gadgets import DraggableLine, DraggablePoint
except ImportError:
    from draggable_gadgets import DraggableLine, DraggablePoint


pg.setConfigOptions(antialias=True, imageAxisOrder="row-major", background="w", foreground="k")


class XarrayViewer(QMainWindow):
    def __init__(self, parent=None, xr_data: xr.DataArray = None):
        super().__init__(parent)
        if __package__:
            uipath = files(__package__) / "xr_gui.ui"
        else:
            uipath = Path(__file__).resolve().parent / "xr_gui.ui"
        uic.loadUi(str(uipath), self)

        self.xr_data = xr_data
        if self.xr_data is None:
            raise ValueError("xr_data must be provided")

        self.x_combo: QComboBox = self.x_combo
        self.y_combo: QComboBox = self.y_combo
        self.z_combo: QComboBox = self.z_combo
        self.sliders_area = self.sliders_area
        self.export1_btn = self.findChild(QtWidgets.QPushButton, "export1_btn")
        self.plt1_sym_clim_chkbx: QCheckBox = self.findChild(QtWidgets.QCheckBox, "plt1_sym_clim_chkbx")
        self.plt1_fix_clim_chkbx: QCheckBox = self.findChild(QtWidgets.QCheckBox, "plt1_fix_clim_chkbx")
        self.plt2_sym_clim_chkbx: QCheckBox = self.findChild(QtWidgets.QCheckBox, "plt2_sym_clim_chkbx")
        self.plt2_fix_clim_chkbx: QCheckBox = self.findChild(QtWidgets.QCheckBox, "plt2_fix_clim_chkbx")

        dims = list(self.xr_data.dims)
        for cb in (self.x_combo, self.y_combo, self.z_combo):
            cb.clear()
            cb.addItems(dims)
        x_default = "x" if "x" in dims else dims[-1]
        y_default = "y" if "y" in dims else (dims[-2] if len(dims) >= 2 else dims[0])
        z_default = next((d for d in dims if d not in (x_default, y_default)), dims[0])
        self.x_combo.setCurrentText(x_default)
        self.y_combo.setCurrentText(y_default)
        self.z_combo.setCurrentText(z_default)

        (
            self.xy_plot,
            self.xy_plot_item,
            self.xy_image,
            self.xy_cbar,
        ) = self._build_image_panel("xyplot_container", cmap_name="viridis")
        (
            self.zd_plot,
            self.zd_plot_item,
            self.zd_image,
            self.zd_cbar,
        ) = self._build_image_panel("zcut_plot_container", cmap_name="magma")
        self.zp_plot, self.zp_plot_item = self._build_curve_panel("z_point_plot_container")
        self.zt_plot, self.zt_plot_item = self._build_curve_panel("z_t_plot_container")
        self.xp_plot, self.xp_plot_item = self._build_curve_panel("xy_profile_container")

        self._xy_fixed_clim = None
        self._zd_fixed_clim = None
        self._xy_bounds = None
        self._zd_bounds = None
        self._line_length = None
        self._interp_cache = None
        self._vline_x = None
        self._vline_updating = False
        self._axis_change_in_progress = False
        self._last_valid_axes = (x_default, y_default, z_default)
        self._overlay_axes = None

        self.draggable = None
        self.free_point = None
        self.vline = None

        self.dim_controls = {}
        self._build_dim_controls()

        self.n_samp = 200

        self._zd_timer = QTimer(self)
        self._zd_timer.setSingleShot(True)
        self._zd_timer.setInterval(40)
        self._zd_timer.timeout.connect(self._update_zdistance_plot)

        self._zp_timer = QTimer(self)
        self._zp_timer.setSingleShot(True)
        self._zp_timer.setInterval(40)
        self._zp_timer.timeout.connect(self._update_zpoint_plot)

        self._zt_timer = QTimer(self)
        self._zt_timer.setSingleShot(True)
        self._zt_timer.setInterval(40)
        self._zt_timer.timeout.connect(self._update_zt_plot)

        self._xp_timer = QTimer(self)
        self._xp_timer.setSingleShot(True)
        self._xp_timer.setInterval(40)
        self._xp_timer.timeout.connect(self._update_xy_profile)

        self.x_combo.currentTextChanged.connect(lambda _text: self._on_axis_selection_changed("x"))
        self.y_combo.currentTextChanged.connect(lambda _text: self._on_axis_selection_changed("y"))
        self.z_combo.currentTextChanged.connect(lambda _text: self._on_axis_selection_changed("z"))
        if self.export1_btn is not None:
            self.export1_btn.clicked.connect(self._export_current_snapshot)
        if self.plt1_sym_clim_chkbx is not None:
            self.plt1_sym_clim_chkbx.stateChanged.connect(self._on_xy_clim_mode_changed)
        if self.plt1_fix_clim_chkbx is not None:
            self.plt1_fix_clim_chkbx.stateChanged.connect(self._on_xy_clim_mode_changed)
        if self.plt2_sym_clim_chkbx is not None:
            self.plt2_sym_clim_chkbx.stateChanged.connect(self._on_zd_clim_mode_changed)
        if self.plt2_fix_clim_chkbx is not None:
            self.plt2_fix_clim_chkbx.stateChanged.connect(self._on_zd_clim_mode_changed)

        self.update_plots()

    def _build_image_panel(self, host_name, cmap_name):
        host = self.findChild(QtWidgets.QWidget, host_name)
        layout = host.layout() or QtWidgets.QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot_item = plot.getPlotItem()
        plot_item.getViewBox().setDefaultPadding(0.0)
        plot_item.showGrid(x=False, y=False)
        layout.addWidget(plot)

        image = pg.ImageItem(axisOrder="row-major")
        plot_item.addItem(image)

        cmap = pg.colormap.get(cmap_name)
        cbar = pg.ColorBarItem(values=(0.0, 1.0), colorMap=cmap, interactive=False)
        cbar.setImageItem(image, insert_in=plot_item)
        return plot, plot_item, image, cbar

    def _build_curve_panel(self, host_name):
        host = self.findChild(QtWidgets.QWidget, host_name)
        layout = host.layout() or QtWidgets.QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot_item = plot.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.15)
        layout.addWidget(plot)
        return plot, plot_item

    def _on_axis_selection_changed(self, changed_axis):
        if self._axis_change_in_progress:
            return

        combos = {"x": self.x_combo, "y": self.y_combo, "z": self.z_combo}
        previous = dict(zip(("x", "y", "z"), self._last_valid_axes))
        current = {name: combo.currentText() for name, combo in combos.items()}

        # Selecting a dimension already used by another axis swaps the two
        # assignments. This keeps every intermediate UI state valid and makes
        # changing X/Y/Z possible without a temporary duplicate selection.
        duplicate_axis = next(
            (name for name in ("x", "y", "z") if name != changed_axis and current[name] == current[changed_axis]),
            None,
        )
        if duplicate_axis is not None:
            self._axis_change_in_progress = True
            try:
                other_combo = combos[duplicate_axis]
                signals_were_blocked = other_combo.blockSignals(True)
                try:
                    other_combo.setCurrentText(previous[changed_axis])
                finally:
                    other_combo.blockSignals(signals_were_blocked)
            finally:
                self._axis_change_in_progress = False
            current[duplicate_axis] = previous[changed_axis]
            self.statusBar().showMessage(
                f"Swapped {changed_axis.upper()} and {duplicate_axis.upper()} to keep X, Y, and Z distinct",
                5000,
            )

        old_x, old_y, _old_z = self._last_valid_axes
        self._last_valid_axes = (current["x"], current["y"], current["z"])
        if (current["x"], current["y"]) != (old_x, old_y):
            self._build_dim_controls()
        self._interp_cache = None
        self.update_plots()

    def _on_xy_clim_mode_changed(self):
        self._xy_fixed_clim = None
        self._update_xyplot()

    def _on_zd_clim_mode_changed(self):
        self._zd_fixed_clim = None
        self._update_zdistance_plot()

    def _is_numeric_coord(self, dim):
        try:
            vals = np.asarray(self.xr_data.coords[dim].values)
            vals.astype(float)
            return True
        except Exception:
            return False

    def _build_dim_controls(self):
        previous_indices = {
            dim: self._index_for_dim(dim)
            for dim in self.dim_controls
        }
        old = self.sliders_area.widget()
        if old is not None:
            old.setParent(None)

        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(6)

        self.dim_controls = {}
        x_dim = self.x_combo.currentText()
        y_dim = self.y_combo.currentText()

        for dim in self.xr_data.dims:
            if dim in (x_dim, y_dim):
                continue

            n = int(self.xr_data.sizes[dim])
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            name_label = QLabel(dim)
            name_label.setStyleSheet("font-weight: 600;")
            row_layout.addWidget(name_label)

            if n <= 1:
                try:
                    val = self.xr_data.coords[dim].values[0]
                    txt = f"index 0 (= {val})"
                except Exception:
                    txt = "index 0"
                val_label = QLabel(txt)
                row_layout.addWidget(val_label)
                self.dim_controls[dim] = {"type": "label", "widget": val_label, "size": n}
            else:
                if self._is_numeric_coord(dim):
                    slider = QSlider(Qt.Horizontal)
                    slider.setMinimum(0)
                    slider.setMaximum(n - 1)
                    initial_index = max(0, min(int(previous_indices.get(dim, 0)), n - 1))
                    slider.setValue(initial_index)
                    value_label = QLabel(self._format_dim_value(dim, initial_index))

                    def _on_slide(v, d=dim, lbl=value_label):
                        lbl.setText(self._format_dim_value(d, v))
                        self._interp_cache = None
                        self.update_plots()

                    slider.valueChanged.connect(_on_slide)
                    row_layout.addWidget(slider)
                    row_layout.addWidget(value_label)
                    self.dim_controls[dim] = {
                        "type": "slider",
                        "widget": slider,
                        "value_label": value_label,
                        "size": n,
                    }
                else:
                    combo = QComboBox()
                    try:
                        items = [str(v) for v in list(self.xr_data.coords[dim].values)]
                    except Exception:
                        items = [str(i) for i in range(n)]
                    combo.addItems(items)
                    initial_index = max(0, min(int(previous_indices.get(dim, 0)), n - 1))
                    combo.setCurrentIndex(initial_index)

                    def _on_combo(_v, d=dim):
                        self._interp_cache = None
                        self.update_plots()

                    combo.currentIndexChanged.connect(_on_combo)
                    row_layout.addWidget(combo)
                    self.dim_controls[dim] = {"type": "combo", "widget": combo, "size": n}

            form.addRow(row_widget)

        self.sliders_area.setWidget(container)
        self.sliders_area.setWidgetResizable(True)

    def _format_dim_value(self, dim, idx):
        try:
            val = self.xr_data.coords[dim].values[int(idx)]
            if np.isscalar(val):
                return f"index {idx} (= {val})"
            return f"index {idx}"
        except Exception:
            return f"index {idx}"

    def _index_for_dim(self, dim):
        info = self.dim_controls.get(dim)
        if info is None:
            return 0
        if info["type"] == "slider":
            return int(info["widget"].value())
        if info["type"] == "combo":
            return int(info["widget"].currentIndex())
        return 0

    @staticmethod
    def _finite_minmax(values):
        arr = np.asarray(values, dtype=float)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return None
        return float(np.min(finite)), float(np.max(finite))

    @classmethod
    def _compute_clim(cls, values, symmetric=False):
        mm = cls._finite_minmax(values)
        if mm is None:
            return None
        vmin, vmax = mm
        if symmetric:
            vmax_abs = max(abs(vmin), abs(vmax))
            return -float(vmax_abs), float(vmax_abs)
        return float(vmin), float(vmax)

    def _resolve_clim(self, values, symmetric, fixed, stored):
        if fixed and stored is not None:
            return stored
        return self._compute_clim(values, symmetric=symmetric)

    @staticmethod
    def _apply_levels(image_item, colorbar, clim):
        if clim is None:
            return
        vmin, vmax = clim
        if not (np.isfinite(vmin) and np.isfinite(vmax) and vmax > vmin):
            return
        image_item.setLevels((vmin, vmax))
        if colorbar is not None:
            try:
                colorbar.setLevels((vmin, vmax))
            except TypeError:
                colorbar.setLevels(vmin, vmax)

    def _coord_value_for_dim(self, dim, idx):
        try:
            return self.xr_data.coords[dim].values[int(idx)]
        except Exception:
            return idx

    @staticmethod
    def _json_ready(value):
        if isinstance(value, dict):
            return {str(k): XarrayViewer._json_ready(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [XarrayViewer._json_ready(v) for v in value]
        if isinstance(value, np.ndarray):
            return XarrayViewer._json_ready(value.tolist())
        if isinstance(value, np.generic):
            return value.item()
        if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
            try:
                return value.tolist()
            except Exception:
                pass
        return value

    def _serialize_dataarray(self, da: xr.DataArray):
        return {
            "name": da.name,
            "dims": list(da.dims),
            "shape": [int(v) for v in da.shape],
            "coords": {dim: self._json_ready(np.asarray(da.coords[dim].values)) for dim in da.dims},
            "values": self._json_ready(np.asarray(da.values)),
        }

    def _collect_current_state(self):
        dim_controls = {}
        for dim in self.xr_data.dims:
            idx = int(self._index_for_dim(dim))
            dim_controls[dim] = {
                "index": idx,
                "coord_value": self._json_ready(self._coord_value_for_dim(dim, idx)),
            }

        line = None
        line_point = None
        if self.draggable is not None:
            x0, y0, x1, y1 = self.draggable.get_points()
            t = float(self.draggable.get_t())
            line = {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "t": t}
            line_point = {
                "x": float(x0 + t * (x1 - x0)),
                "y": float(y0 + t * (y1 - y0)),
            }

        free_point = None
        if self.free_point is not None:
            px, py = self.free_point.get_xy()
            free_point = {"x": float(px), "y": float(py)}

        return {
            "selected_dims": {
                "x": self.x_combo.currentText(),
                "y": self.y_combo.currentText(),
                "z": self.z_combo.currentText(),
            },
            "dim_controls": dim_controls,
            "line_cut": line,
            "line_point": line_point,
            "free_point": free_point,
            "zdistance_vline_x": None if self._vline_x is None else float(self._vline_x),
        }

    def _build_export_payload(self):
        z_dim = self.z_combo.currentText()
        xy_da = self._slice_for_xyimage()
        zd_da = self._build_z_distance()
        s_profile, vals_profile = self._build_xy_profile()

        if self.free_point is not None:
            free_x, free_y = self.free_point.get_xy()
        else:
            free_x = free_y = None

        z_coords = self._coord_axis(z_dim)
        self._ensure_interpolators()
        interps = self._interp_cache["interps"]

        zp_vals = [self._interp_at_point(interp, free_y, free_x) for interp in interps] if free_x is not None else []

        if self.draggable is not None:
            t = float(self.draggable.get_t())
            x0, y0, x1, y1 = self.draggable.get_points()
            line_x = x0 + t * (x1 - x0)
            line_y = y0 + t * (y1 - y0)
            zt_vals = [self._interp_at_point(interp, line_y, line_x) for interp in interps]
        else:
            line_x = line_y = None
            zt_vals = []

        payload = {
            "format": "XarrayViewer export",
            "format_version": 1,
            "state": self._collect_current_state(),
            "source_data": {
                "dims": list(self.xr_data.dims),
                "shape": [int(v) for v in self.xr_data.shape],
                "coords": {
                    dim: self._json_ready(np.asarray(self.xr_data.coords[dim].values))
                    for dim in self.xr_data.dims
                },
            },
            "plots": {
                "xy_map": self._serialize_dataarray(xy_da),
                "z_vs_distance": self._serialize_dataarray(zd_da),
                "xy_profile": {
                    "distance": self._json_ready(np.asarray(s_profile)),
                    "value": self._json_ready(np.asarray(vals_profile)),
                    "z_dim": z_dim,
                    "z_index": int(self._index_for_dim(z_dim)),
                    "z_coord_value": self._json_ready(self._coord_value_for_dim(z_dim, self._index_for_dim(z_dim))),
                },
                "z_at_free_point": {
                    "point": None if free_x is None else {"x": float(free_x), "y": float(free_y)},
                    "z_dim": z_dim,
                    "z_coord": self._json_ready(z_coords),
                    "value": self._json_ready(np.asarray(zp_vals)),
                },
                "z_at_line_point": {
                    "point": None if line_x is None else {"x": float(line_x), "y": float(line_y)},
                    "z_dim": z_dim,
                    "z_coord": self._json_ready(z_coords),
                    "value": self._json_ready(np.asarray(zt_vals)),
                },
            },
        }
        return self._json_ready(payload)

    def _export_current_snapshot(self):
        try:
            payload = self._build_export_payload()
            x_dim = self.x_combo.currentText()
            y_dim = self.y_combo.currentText()
            z_dim = self.z_combo.currentText()
            default_name = f"xarrayviewer_export_{x_dim}_{y_dim}_{z_dim}.json"
            out_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Current Viewer State",
                str(Path.cwd() / default_name),
                "JSON Files (*.json);;All Files (*)",
            )
            if not out_path:
                return
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self.statusBar().showMessage(f"Exported current viewer data to {out_path}", 8000)
        except Exception:
            print("Export failed:\n", traceback.format_exc())
            self.statusBar().showMessage("Export failed - see console", 8000)

    def _slice_for_xyimage(self):
        x_dim = self.x_combo.currentText()
        y_dim = self.y_combo.currentText()
        indexers = {}
        for d in self.xr_data.dims:
            if d not in (x_dim, y_dim):
                indexers[d] = self._index_for_dim(d)
        da = self.xr_data.isel(**indexers).transpose(y_dim, x_dim)
        if da.ndim != 2:
            raise ValueError("XY slice is not 2D")
        return da

    def _current_cache_key(self):
        x_dim = self.x_combo.currentText()
        y_dim = self.y_combo.currentText()
        z_dim = self.z_combo.currentText()
        fixed = tuple((d, self._index_for_dim(d)) for d in self.xr_data.dims if d not in (x_dim, y_dim, z_dim))
        return x_dim, y_dim, z_dim, fixed

    def _ensure_interpolators(self):
        key = self._current_cache_key()
        if self._interp_cache is not None and self._interp_cache.get("key") == key:
            return

        x_dim, y_dim, z_dim, fixed = key
        x_coords_all = np.asarray(self.xr_data.coords[x_dim].values, dtype=float)
        y_coords_all = np.asarray(self.xr_data.coords[y_dim].values, dtype=float)

        x_grid = x_coords_all
        y_grid = y_coords_all

        sort_needed = not (np.all(np.diff(x_grid) > 0) and np.all(np.diff(y_grid) > 0))
        if sort_needed:
            x_order = np.argsort(x_grid)
            y_order = np.argsort(y_grid)
            x_grid = x_grid[x_order]
            y_grid = y_grid[y_order]
        else:
            x_order = None
            y_order = None

        base_indexers = {d: idx for (d, idx) in fixed}
        nz = int(self.xr_data.sizes[z_dim])
        interps = []
        for zi in range(nz):
            indexers = dict(base_indexers)
            indexers[z_dim] = zi
            plane = self.xr_data.isel(**indexers).transpose(y_dim, x_dim)
            vals2d = np.asarray(plane.values, dtype=float)
            if sort_needed:
                vals2d = vals2d[np.ix_(y_order, x_order)]
            interp = RegularGridInterpolator((y_grid, x_grid), vals2d, bounds_error=False, fill_value=np.nan)
            interps.append(interp)

        self._interp_cache = {
            "key": key,
            "x_grid": x_grid,
            "y_grid": y_grid,
            "sort_needed": sort_needed,
            "x_order": x_order,
            "y_order": y_order,
            "interps": interps,
        }

    @staticmethod
    def _interp_at_point(interp, y, x):
        pt = np.array([[float(y), float(x)]], dtype=float)
        out = np.asarray(interp(pt), dtype=float).reshape(-1)
        if out.size == 0:
            return float("nan")
        return float(out[0])

    def _build_z_distance(self):
        z_dim = self.z_combo.currentText()
        z_vals = self._coord_axis(z_dim)

        if self.draggable is None:
            raise ValueError("draggable line not initialized")
        x0, y0, x1, y1 = self.draggable.get_points()

        dx, dy = x1 - x0, y1 - y0
        L = float(np.hypot(dx, dy))
        if L == 0:
            raise ValueError("line length is zero")
        s = np.linspace(0.0, L, self.n_samp)
        t = s / L
        xs = x0 + t * dx
        ys = y0 + t * dy
        sample_pts = np.column_stack([ys, xs])

        self._ensure_interpolators()
        interps = self._interp_cache["interps"]
        out = np.empty((len(interps), self.n_samp), dtype=float)
        for zi, interp in enumerate(interps):
            out[zi, :] = interp(sample_pts)

        return xr.DataArray(out, coords={z_dim: z_vals, "distance": s}, dims=[z_dim, "distance"])

    def _build_xy_profile(self):
        z_dim = self.z_combo.currentText()
        if self.draggable is None:
            raise ValueError("draggable line not initialized")

        x0, y0, x1, y1 = self.draggable.get_points()
        dx, dy = x1 - x0, y1 - y0
        L = float(np.hypot(dx, dy))
        if L == 0:
            raise ValueError("line length is zero")

        s = np.linspace(0.0, L, self.n_samp)
        t = s / L
        xs = x0 + t * dx
        ys = y0 + t * dy
        sample_pts = np.column_stack([ys, xs])

        self._ensure_interpolators()
        interps = self._interp_cache["interps"]
        z_idx = int(self._index_for_dim(z_dim))
        z_idx = max(0, min(z_idx, len(interps) - 1))
        vals = interps[z_idx](sample_pts).astype(float)
        return s, vals

    def _coord_axis(self, dim):
        try:
            return np.asarray(self.xr_data.coords[dim].values, dtype=float)
        except Exception:
            return np.arange(int(self.xr_data.sizes[dim]), dtype=float)

    @staticmethod
    def _orient_for_display(plane2d, col_coords, row_coords):
        """Reorder a (row, col) plane so the displayed image is data-accurate.

        pyqtgraph ImageItem in row-major mode draws row index 0 at the
        *bottom* edge of its rect and column index 0 on the *left* edge,
        with index positions increasing linearly toward the max coordinate.
        That is the correct orientation only when the coordinate arrays are
        ascending.  To always show array cell (r, c) at the physical
        coordinate (col_coords[c], row_coords[r]) - the same convention the
        matplotlib/xarray viewer uses (pixels centered on coordinates) - we
        reorder rows and columns into ascending coordinate order, exactly
        mirroring the sorting applied to the interpolator grids in
        ``_ensure_interpolators``.

        Coordinates already in ascending order are left untouched, so for the
        common ascending case this returns the plane unchanged (no flipud).
        """
        arr = np.asarray(plane2d)
        xc = np.asarray(col_coords, dtype=float)
        yc = np.asarray(row_coords, dtype=float)
        nx, ny = arr.shape[1], arr.shape[0]
        if nx != xc.size or ny != yc.size:
            raise ValueError("plane shape does not match coordinate lengths")
        if not np.all(np.diff(xc) > 0):
            arr = arr[:, np.argsort(xc, kind="stable")]
        if not np.all(np.diff(yc) > 0):
            arr = arr[np.argsort(yc, kind="stable"), :]
        return arr

    @staticmethod
    def _bounds_from_axis(x_vals, y_vals):
        xmin = float(np.min(x_vals))
        xmax = float(np.max(x_vals))
        ymin = float(np.min(y_vals))
        ymax = float(np.max(y_vals))
        return xmin, xmax, ymin, ymax

    @staticmethod
    def _pixel_edge_bounds(coords):
        """Return image edges with coordinate values at the pixel centers."""
        vals = np.sort(np.asarray(coords, dtype=float))
        if vals.size == 0:
            raise ValueError("image coordinate axis is empty")
        if vals.size == 1:
            return float(vals[0] - 0.5), float(vals[0] + 0.5)

        # ImageItem uses one linear transform for the whole image.  The scan
        # axes used here are uniform; use the endpoint cell widths so that the
        # first and last pixel centers land exactly on their coordinates.
        lo = float(vals[0] - 0.5 * (vals[1] - vals[0]))
        hi = float(vals[-1] + 0.5 * (vals[-1] - vals[-2]))
        return lo, hi

    @classmethod
    def _image_bounds_from_axis(cls, x_vals, y_vals):
        xmin, xmax = cls._pixel_edge_bounds(x_vals)
        ymin, ymax = cls._pixel_edge_bounds(y_vals)
        return xmin, xmax, ymin, ymax

    @staticmethod
    def _image_rect(bounds):
        xmin, xmax, ymin, ymax = bounds
        width = xmax - xmin if xmax != xmin else 1.0
        height = ymax - ymin if ymax != ymin else 1.0
        return QtCore.QRectF(xmin, ymin, width, height)

    def _new_default_line(self, bounds):
        xmin, xmax, ymin, ymax = bounds
        y0 = y1 = ymin + 0.5 * (ymax - ymin)
        x0 = xmin + 0.2 * (xmax - xmin)
        x1 = xmax - 0.2 * (xmax - xmin)
        return x0, y0, x1, y1

    def update_plots(self):
        try:
            self._update_xyplot()
        except Exception:
            print("XY plot failed:\n", traceback.format_exc())
            self.statusBar().showMessage("XY failed - see console", 8000)

        try:
            self._update_zdistance_plot()
        except Exception:
            print("Z-distance plot failed:\n", traceback.format_exc())
            self.statusBar().showMessage("Z-distance failed - see console", 8000)

        try:
            self._update_zpoint_plot()
        except Exception:
            print("Z-point plot failed:\n", traceback.format_exc())
            self._show_curve_placeholder(self.zp_plot_item, "Z-point failed - see console")

        try:
            self._update_zt_plot()
        except Exception:
            print("Z-t plot failed:\n", traceback.format_exc())
            self._show_curve_placeholder(self.zt_plot_item, "Z-t failed - see console")

        try:
            self._update_xy_profile()
        except Exception:
            print("XY profile plot failed:\n", traceback.format_exc())
            self._show_curve_placeholder(self.xp_plot_item, "XY profile failed - see console")

    def _update_xyplot(self):
        da2d = self._slice_for_xyimage()
        x_dim = self.x_combo.currentText()
        y_dim = self.y_combo.currentText()

        same_coordinate_system = self._overlay_axes == (x_dim, y_dim)
        saved_line = self.draggable.get_points() if self.draggable is not None and same_coordinate_system else None
        saved_t = self.draggable.get_t() if self.draggable is not None else 0.5
        saved_pt = self.free_point.get_xy() if self.free_point is not None and same_coordinate_system else None

        if self.draggable is not None:
            self.draggable.disconnect()
            self.draggable = None
        if self.free_point is not None:
            self.free_point.disconnect()
            self.free_point = None

        x_vals = self._coord_axis(x_dim)
        y_vals = self._coord_axis(y_dim)
        self._xy_bounds = self._bounds_from_axis(x_vals, y_vals)

        values = np.asarray(da2d.values, dtype=float)
        # Orient rows/cols to the actual coordinate values so what the user
        # sees matches where the data really is (no blind flipud).
        display = self._orient_for_display(values, x_vals, y_vals)
        self.xy_image.setImage(display, autoLevels=False)
        xy_image_bounds = self._image_bounds_from_axis(x_vals, y_vals)
        self.xy_image.setRect(self._image_rect(xy_image_bounds))

        xy_clim = self._resolve_clim(
            values,
            symmetric=bool(self.plt1_sym_clim_chkbx.isChecked()) if self.plt1_sym_clim_chkbx is not None else False,
            fixed=bool(self.plt1_fix_clim_chkbx.isChecked()) if self.plt1_fix_clim_chkbx is not None else False,
            stored=self._xy_fixed_clim,
        )
        self._apply_levels(self.xy_image, self.xy_cbar, xy_clim)
        self._xy_fixed_clim = xy_clim if self.plt1_fix_clim_chkbx is not None and self.plt1_fix_clim_chkbx.isChecked() else None

        xmin, xmax, ymin, ymax = xy_image_bounds
        self.xy_plot_item.setLabel("bottom", x_dim)
        self.xy_plot_item.setLabel("left", y_dim)
        self.xy_plot_item.setTitle("XY map")
        self.xy_plot_item.getViewBox().setLimits(xMin=xmin, xMax=xmax, yMin=ymin, yMax=ymax)
        self.xy_plot_item.setXRange(xmin, xmax, padding=0)
        self.xy_plot_item.setYRange(ymin, ymax, padding=0)

        if saved_line is None:
            x0, y0, x1, y1 = self._new_default_line(self._xy_bounds)
        else:
            x0, y0, x1, y1 = saved_line

        def _on_line_move(x0m, y0m, x1m, y1m, t):
            L = float(np.hypot(x1m - x0m, y1m - y0m))
            self._line_length = L if L > 0 else None
            if self._line_length is not None:
                self._vline_x = float(t) * self._line_length
                self._sync_vline_item()
            self._zd_timer.start()
            self._zt_timer.start()
            self._xp_timer.start()

        def _on_line_release(x0m, y0m, x1m, y1m, t):
            _on_line_move(x0m, y0m, x1m, y1m, t)
            self._update_zdistance_plot()
            self._update_zt_plot()
            self._update_xy_profile()

        self.draggable = DraggableLine(
            self.xy_plot_item,
            x0,
            y0,
            x1,
            y1,
            on_move=_on_line_move,
            on_release=_on_line_release,
            t_init=saved_t,
        )
        self.draggable.set_bounds(self._xy_bounds)
        self.draggable.draw()

        if saved_pt is None:
            px = 0.5 * (xmin + xmax)
            py = 0.5 * (ymin + ymax)
        else:
            px, py = saved_pt

        self.free_point = DraggablePoint(
            self.xy_plot_item,
            px,
            py,
            color="yellow",
            on_move=lambda _x, _y: self._zp_timer.start(),
            on_release=lambda _x, _y: self._update_zpoint_plot(),
        )
        self.free_point.set_bounds(self._xy_bounds)
        self.free_point.draw()
        self._overlay_axes = (x_dim, y_dim)

    def _sync_vline_item(self):
        if self.vline is None or self._vline_x is None:
            return
        self._vline_updating = True
        try:
            self.vline.setValue(float(self._vline_x))
        finally:
            self._vline_updating = False

    def _update_zdistance_plot(self):
        da_zd = self._build_z_distance()
        z_dim = self.z_combo.currentText()
        z_vals = self._coord_axis(z_dim)
        dist_vals = np.asarray(da_zd.coords["distance"].values, dtype=float)
        self._zd_bounds = self._bounds_from_axis(dist_vals, z_vals)

        values = np.asarray(da_zd.values, dtype=float)
        # Row axis is the z dimension: keep z[i] at its true vertical position
        # (mirrors the xy map fix; "distance" columns are always ascending).
        display = self._orient_for_display(values, dist_vals, z_vals)
        self.zd_image.setImage(display, autoLevels=False)
        zd_image_bounds = self._image_bounds_from_axis(dist_vals, z_vals)
        self.zd_image.setRect(self._image_rect(zd_image_bounds))

        zd_clim = self._resolve_clim(
            values,
            symmetric=bool(self.plt2_sym_clim_chkbx.isChecked()) if self.plt2_sym_clim_chkbx is not None else False,
            fixed=bool(self.plt2_fix_clim_chkbx.isChecked()) if self.plt2_fix_clim_chkbx is not None else False,
            stored=self._zd_fixed_clim,
        )
        self._apply_levels(self.zd_image, self.zd_cbar, zd_clim)
        self._zd_fixed_clim = zd_clim if self.plt2_fix_clim_chkbx is not None and self.plt2_fix_clim_chkbx.isChecked() else None

        xmin, xmax, ymin, ymax = zd_image_bounds
        self.zd_plot_item.setLabel("bottom", "distance")
        self.zd_plot_item.setLabel("left", z_dim)
        self.zd_plot_item.setTitle("Z vs distance")
        self.zd_plot_item.getViewBox().setLimits(xMin=xmin, xMax=xmax, yMin=ymin, yMax=ymax)
        self.zd_plot_item.setXRange(xmin, xmax, padding=0)
        self.zd_plot_item.setYRange(ymin, ymax, padding=0)

        if self.draggable is not None:
            x0, y0, x1, y1 = self.draggable.get_points()
            L = float(np.hypot(x1 - x0, y1 - y0))
            self._line_length = L if L > 0 else None

        if self.draggable is not None and self._line_length:
            self._vline_x = float(self.draggable.get_t()) * self._line_length
        elif self._vline_x is None:
            self._vline_x = 0.5 * (xmin + xmax)
        dist_min, dist_max, _, _ = self._zd_bounds
        self._vline_x = float(min(max(self._vline_x, dist_min), dist_max))

        if self.vline is None:
            self.vline = pg.InfiniteLine(
                pos=self._vline_x,
                angle=90,
                pen=pg.mkPen("w", width=2),
                movable=True,
                bounds=[dist_min, dist_max],
            )
            self.vline.setZValue(20)
            self.zd_plot_item.addItem(self.vline)
            self.vline.sigPositionChanged.connect(self._on_vline_move)
            self.vline.sigPositionChangeFinished.connect(self._on_vline_release)
        else:
            self.vline.setBounds([dist_min, dist_max])
            self._sync_vline_item()

    def _on_vline_move(self, line):
        if self._vline_updating:
            return
        self._vline_x = float(line.value())
        if self.draggable is not None and self._line_length:
            t = 0.0 if self._line_length <= 0 else max(0.0, min(1.0, self._vline_x / self._line_length))
            self.draggable.set_t(t)
            self._zt_timer.start()
            self._xp_timer.start()

    def _on_vline_release(self, line):
        self._on_vline_move(line)
        self._update_zt_plot()
        self._update_xy_profile()

    def _update_zpoint_plot(self):
        z_dim = self.z_combo.currentText()
        z_vals = self._coord_axis(z_dim)

        if self.free_point is not None:
            x, y = self.free_point.get_xy()
        elif self._xy_bounds is not None:
            xmin, xmax, ymin, ymax = self._xy_bounds
            x = 0.5 * (xmin + xmax)
            y = 0.5 * (ymin + ymax)
        else:
            raise ValueError("XY bounds not ready")

        self._ensure_interpolators()
        interps = self._interp_cache["interps"]
        vals = np.empty(len(interps), dtype=float)
        for i, interp in enumerate(interps):
            vals[i] = self._interp_at_point(interp, y, x)

        self.zp_plot_item.clear()
        self.zp_plot_item.plot(z_vals, vals, pen=pg.mkPen("#1f77b4", width=2))
        self.zp_plot_item.setLabel("bottom", z_dim)
        self.zp_plot_item.setLabel("left", "value")
        self.zp_plot_item.setTitle(f"value vs {z_dim} at x={x:.3g}, y={y:.3g}")

    def _update_zt_plot(self):
        z_dim = self.z_combo.currentText()
        z_vals = self._coord_axis(z_dim)

        if self.draggable is None:
            self._show_curve_placeholder(self.zt_plot_item, "line not ready")
            return

        t = float(self.draggable.get_t())
        x0, y0, x1, y1 = self.draggable.get_points()
        x = x0 + t * (x1 - x0)
        y = y0 + t * (y1 - y0)

        self._ensure_interpolators()
        interps = self._interp_cache["interps"]
        vals = np.empty(len(interps), dtype=float)
        for i, interp in enumerate(interps):
            vals[i] = self._interp_at_point(interp, y, x)

        self.zt_plot_item.clear()
        self.zt_plot_item.plot(z_vals, vals, pen=pg.mkPen("#1f77b4", width=2))
        self.zt_plot_item.setLabel("bottom", z_dim)
        self.zt_plot_item.setLabel("left", "value")
        self.zt_plot_item.setTitle(f"value vs {z_dim} at line-point (t={t:.3f})")

    def _update_xy_profile(self):
        s, vals = self._build_xy_profile()
        z_dim = self.z_combo.currentText()
        z_idx = int(self._index_for_dim(z_dim))

        self.xp_plot_item.clear()
        self.xp_plot_item.plot(s, vals, pen=pg.mkPen("#1f77b4", width=2))
        self.xp_plot_item.setLabel("bottom", "distance")
        self.xp_plot_item.setLabel("left", "value")
        try:
            z_val = self.xr_data.coords[z_dim].values[z_idx]
            self.xp_plot_item.setTitle(f"value vs distance @ {z_dim}[{z_idx}]={z_val}")
        except Exception:
            self.xp_plot_item.setTitle(f"value vs distance @ {z_dim}[{z_idx}]")

    def _show_curve_placeholder(self, plot_item, msg="void"):
        plot_item.clear()
        plot_item.setTitle(msg)
        self.statusBar().showMessage(msg, 8000)


if __name__ == "__main__":
    import sys

    base_dir = Path(__file__).resolve().parent
    data_file = loadmat(base_dir / "A22B6_gf_QWP189to360s9_10umstrain.mat")
    data = data_file["level4_result"]

    void_axis = data_file["level0_index0_targets"][0]
    sk_axis = data_file["level1_index0_targets"][0]
    qwp_axis = data_file["level2_index0_targets"][0]
    ao1_axis = data_file["level3_index0_targets"][0]
    ao0_axis = data_file["level4_index0_targets"][0]

    data_t = np.transpose(data)
    pcy_data = np.transpose(data_t[1])

    pcy = xr.DataArray(
        pcy_data,
        coords={"void": void_axis, "wl": sk_axis, "qwp": qwp_axis, "y": ao1_axis, "x": ao0_axis},
        dims=["void", "wl", "qwp", "y", "x"],
    )

    app = QtWidgets.QApplication(sys.argv)
    window = XarrayViewer(xr_data=pcy)
    window.show()
    sys.exit(app.exec_())
