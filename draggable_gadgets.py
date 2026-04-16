import pyqtgraph as pg


class DraggablePoint:
    def __init__(self, plot_item, x, y, color="yellow", on_move=None, on_release=None):
        self.plot_item = plot_item
        self.on_move = on_move
        self.on_release = on_release
        self._updating = False
        self._bounds = None

        pen = pg.mkPen("k", width=1.5)
        brush = pg.mkBrush(color)
        hover_brush = pg.mkBrush(color)

        self.item = pg.TargetItem(
            pos=(x, y),
            size=12,
            symbol="o",
            pen=pen,
            hoverPen=pen,
            brush=brush,
            hoverBrush=hover_brush,
            movable=True,
        )
        self.item.setZValue(20)
        self.plot_item.addItem(self.item)

        self.item.sigPositionChanged.connect(self._on_move)
        self.item.sigPositionChangeFinished.connect(self._on_release)

    def set_bounds(self, bounds):
        self._bounds = bounds

    def get_xy(self):
        pos = self.item.pos()
        return float(pos.x()), float(pos.y())

    def set_xy(self, x, y):
        self._set_pos(x, y)

    def draw(self):
        pass

    def disconnect(self):
        try:
            self.item.sigPositionChanged.disconnect(self._on_move)
        except Exception:
            pass
        try:
            self.item.sigPositionChangeFinished.disconnect(self._on_release)
        except Exception:
            pass
        try:
            self.plot_item.removeItem(self.item)
        except Exception:
            pass

    def _clamp(self, x, y):
        if self._bounds is None:
            return float(x), float(y)
        xmin, xmax, ymin, ymax = self._bounds
        return (
            min(max(float(x), float(xmin)), float(xmax)),
            min(max(float(y), float(ymin)), float(ymax)),
        )

    def _set_pos(self, x, y):
        x, y = self._clamp(x, y)
        self._updating = True
        try:
            self.item.setPos(x, y)
        finally:
            self._updating = False

    def _on_move(self, _item):
        if self._updating:
            return
        x, y = self.get_xy()
        x, y = self._clamp(x, y)
        if (x, y) != self.get_xy():
            self._set_pos(x, y)
        if callable(self.on_move):
            self.on_move(float(x), float(y))

    def _on_release(self, _item):
        x, y = self.get_xy()
        x, y = self._clamp(x, y)
        if (x, y) != self.get_xy():
            self._set_pos(x, y)
        if callable(self.on_release):
            self.on_release(float(x), float(y))


class DraggableLine:
    def __init__(self, plot_item, x0, y0, x1, y1, on_move=None, on_release=None, t_init=0.5):
        self.plot_item = plot_item
        self.on_move = on_move
        self.on_release = on_release
        self._bounds = None
        self._updating = False
        self._t_margin = 0.06
        self._t = float(max(self._t_margin, min(1.0 - self._t_margin, t_init)))

        self.line_item = pg.PlotCurveItem(pen=pg.mkPen("k", width=2))
        self.line_item.setZValue(10)
        self.plot_item.addItem(self.line_item)

        self.start_item = pg.TargetItem(
            pos=(x0, y0),
            size=12,
            symbol="o",
            pen=pg.mkPen("r", width=2),
            hoverPen=pg.mkPen("r", width=2),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            movable=True,
        )
        self.end_item = pg.TargetItem(
            pos=(x1, y1),
            size=12,
            symbol="o",
            pen=pg.mkPen("k", width=2),
            hoverPen=pg.mkPen("k", width=2),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            movable=True,
        )
        self.mid_item = pg.TargetItem(
            pos=self._point_at_t(self._t, x0, y0, x1, y1),
            size=11,
            symbol="o",
            pen=pg.mkPen("k", width=1.5),
            hoverPen=pg.mkPen("k", width=1.5),
            brush=pg.mkBrush("w"),
            hoverBrush=pg.mkBrush("w"),
            movable=True,
        )

        for item, z in ((self.start_item, 15), (self.end_item, 15), (self.mid_item, 16)):
            item.setZValue(z)
            self.plot_item.addItem(item)

        self.start_item.sigPositionChanged.connect(self._on_endpoint_move)
        self.end_item.sigPositionChanged.connect(self._on_endpoint_move)
        self.mid_item.sigPositionChanged.connect(self._on_mid_move)
        self.start_item.sigPositionChangeFinished.connect(self._on_endpoint_release)
        self.end_item.sigPositionChangeFinished.connect(self._on_endpoint_release)
        self.mid_item.sigPositionChangeFinished.connect(self._on_mid_release)

        self._refresh_line()

    def set_bounds(self, bounds):
        self._bounds = bounds
        x0, y0, x1, y1 = self.get_points()
        self.set_points(x0, y0, x1, y1)

    def get_points(self):
        p0 = self.start_item.pos()
        p1 = self.end_item.pos()
        return float(p0.x()), float(p0.y()), float(p1.x()), float(p1.y())

    def set_points(self, x0, y0, x1, y1, keep_t=True):
        if not keep_t:
            self._t = 0.5
        x0, y0 = self._clamp(x0, y0)
        x1, y1 = self._clamp(x1, y1)
        self._set_item_pos(self.start_item, x0, y0)
        self._set_item_pos(self.end_item, x1, y1)
        self._t = max(self._t_margin, min(1.0 - self._t_margin, self._t))
        xm, ym = self._point_at_t(self._t, x0, y0, x1, y1)
        self._set_item_pos(self.mid_item, xm, ym)
        self._refresh_line()

    def get_t(self):
        return float(self._t)

    def set_t(self, t):
        self._t = max(self._t_margin, min(1.0 - self._t_margin, float(t)))
        x0, y0, x1, y1 = self.get_points()
        xm, ym = self._point_at_t(self._t, x0, y0, x1, y1)
        self._set_item_pos(self.mid_item, xm, ym)
        self._refresh_line()

    def draw(self):
        self._refresh_line()

    def disconnect(self):
        for item, move_cb, rel_cb in (
            (self.start_item, self._on_endpoint_move, self._on_endpoint_release),
            (self.end_item, self._on_endpoint_move, self._on_endpoint_release),
            (self.mid_item, self._on_mid_move, self._on_mid_release),
        ):
            try:
                item.sigPositionChanged.disconnect(move_cb)
            except Exception:
                pass
            try:
                item.sigPositionChangeFinished.disconnect(rel_cb)
            except Exception:
                pass
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass
        try:
            self.plot_item.removeItem(self.line_item)
        except Exception:
            pass

    def _clamp(self, x, y):
        if self._bounds is None:
            return float(x), float(y)
        xmin, xmax, ymin, ymax = self._bounds
        return (
            min(max(float(x), float(xmin)), float(xmax)),
            min(max(float(y), float(ymin)), float(ymax)),
        )

    def _set_item_pos(self, item, x, y):
        self._updating = True
        try:
            item.setPos(float(x), float(y))
        finally:
            self._updating = False

    @staticmethod
    def _point_at_t(t, x0, y0, x1, y1):
        return x0 + t * (x1 - x0), y0 + t * (y1 - y0)

    def _project_param(self, x, y):
        x0, y0, x1, y1 = self.get_points()
        vx, vy = x1 - x0, y1 - y0
        denom = vx * vx + vy * vy
        if denom <= 0:
            return 0.0
        wx, wy = x - x0, y - y0
        t = (wx * vx + wy * vy) / denom
        return max(0.0, min(1.0, float(t)))

    def _refresh_line(self):
        x0, y0, x1, y1 = self.get_points()
        self.line_item.setData([x0, x1], [y0, y1])

    def _emit_move(self):
        if callable(self.on_move):
            x0, y0, x1, y1 = self.get_points()
            self.on_move(x0, y0, x1, y1, self._t)

    def _emit_release(self):
        if callable(self.on_release):
            x0, y0, x1, y1 = self.get_points()
            self.on_release(x0, y0, x1, y1, self._t)

    def _on_endpoint_move(self, _item):
        if self._updating:
            return
        x0, y0, x1, y1 = self.get_points()
        x0, y0 = self._clamp(x0, y0)
        x1, y1 = self._clamp(x1, y1)
        self._set_item_pos(self.start_item, x0, y0)
        self._set_item_pos(self.end_item, x1, y1)
        xm, ym = self._point_at_t(self._t, x0, y0, x1, y1)
        self._set_item_pos(self.mid_item, xm, ym)
        self._refresh_line()
        self._emit_move()

    def _on_endpoint_release(self, _item):
        self._on_endpoint_move(_item)
        self._emit_release()

    def _on_mid_move(self, _item):
        if self._updating:
            return
        xm, ym = self.mid_item.pos().x(), self.mid_item.pos().y()
        xm, ym = self._clamp(xm, ym)
        self._t = max(self._t_margin, min(1.0 - self._t_margin, self._project_param(xm, ym)))
        x0, y0, x1, y1 = self.get_points()
        x_proj, y_proj = self._point_at_t(self._t, x0, y0, x1, y1)
        self._set_item_pos(self.mid_item, x_proj, y_proj)
        self._refresh_line()
        self._emit_move()

    def _on_mid_release(self, _item):
        self._on_mid_move(_item)
        self._emit_release()
