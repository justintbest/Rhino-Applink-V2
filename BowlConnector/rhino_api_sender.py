# -*- coding: utf-8 -*-
# rhino_api_sender_2.py
# Popup panel to push A-Lines / Voids / Aisle groups to, and pull a saved
# Bowl's geometry snapshot from, the Seating Bowl Generator backend.

import json
import threading
import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
import Rhino.Geometry as rg
import Eto.Forms as forms
import Eto.Drawing as drawing
import System
import System.Net
import System.Text

BASE_URL = "https://bowl-backend-x0jz.onrender.com"

# ── Theme ────────────────────────────────────────────────────────────────────
COL_BG       = drawing.Color.FromArgb(26,  26,  26)   # #1A1A1A dark background
COL_SURFACE  = drawing.Color.FromArgb(40,  40,  40)   # #282828 input fields
COL_ACCENT   = drawing.Color.FromArgb(190,  0, 255)   # #BE00FF magenta
COL_TEXT     = drawing.Color.FromArgb(255, 255, 255)   # white
COL_MUTED    = drawing.Color.FromArgb(160, 160, 160)   # grey labels


# ── Geometry helpers ─────────────────────────────────────────────────────────

def coerce_curve(obj):
    geo = obj.CurveGeometry if hasattr(obj, "CurveGeometry") else getattr(obj, "Geometry", None)
    if geo is None:
        return None
    if isinstance(geo, rg.PolylineCurve):
        return geo
    if isinstance(geo, rg.Curve):
        return geo
    return None


def extract_2d_points(raw_pts, is_closed):
    """raw_pts: list of (x, y[, z]) tuples. Returns (points, error)."""
    pts = [(p[0], p[1]) for p in raw_pts]

    if is_closed and len(pts) >= 2:
        if abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6:
            pts = pts[:-1]

    if len(pts) < 3:
        return None, "need at least 3 distinct points (got {0})".format(len(pts))

    return pts, None


def get_preview_points(curve):
    """Return a list of (x, y, z) tuples for the preview, or None."""
    ok, poly = curve.TryGetPolyline()
    if not ok:
        return None
    return [(p.X, p.Y, p.Z) for p in poly]


def closed_curve_points(curve, samples=64):
    """Closed curve -> list of (x, y) tuples, world XY, no repeated end point."""
    if not curve.IsClosed:
        raise RuntimeError("every void loop must be a closed curve")
    ok, pl = curve.TryGetPolyline()
    if ok:
        pts = [(p.X, p.Y) for p in pl]
    else:
        params = curve.DivideByCount(samples, True) or []
        pts = [(curve.PointAt(t).X, curve.PointAt(t).Y) for t in params]
    if len(pts) > 1:
        dx = pts[0][0] - pts[-1][0]
        dy = pts[0][1] - pts[-1][1]
        if (dx * dx + dy * dy) ** 0.5 < 1e-9:
            pts = pts[:-1]
    if len(pts) < 3:
        raise RuntimeError("a loop needs at least 3 distinct points")
    return pts


def open_curve_points(curve, idx, samples=32):
    """Open curve -> list of (x, y) tuples, world XY."""
    if curve.IsClosed:
        raise RuntimeError("curve {0} is closed - aisle centerlines must be OPEN".format(idx + 1))
    ok, pl = curve.TryGetPolyline()
    if ok:
        pts = [(p.X, p.Y) for p in pl]
    else:
        params = curve.DivideByCount(samples, True) or []
        pts = [(curve.PointAt(t).X, curve.PointAt(t).Y) for t in params]
    if len(pts) < 2:
        raise RuntimeError("curve {0} needs at least 2 points".format(idx + 1))
    return pts


# ── HTTP helpers ─────────────────────────────────────────────────────────────

class TimeoutWebClient(System.Net.WebClient):
    """WebClient with a configurable request timeout (plain WebClient has none).
    Used for the Pull tab, where the backend's free-tier host can take
    30-60s to cold-start on the first request of the day."""

    def __init__(self, timeout_ms):
        System.Net.WebClient.__init__(self)
        self._timeout_ms = timeout_ms

    def GetWebRequest(self, address):
        req = System.Net.WebClient.GetWebRequest(self, address)
        req.Timeout = self._timeout_ms
        return req


def _web_client(timeout_ms=None):
    if timeout_ms:
        return TimeoutWebClient(timeout_ms)
    return System.Net.WebClient()


def post_json(url, data, token=None, timeout_ms=None):
    try:
        client = _web_client(timeout_ms)
        client.Headers.Add("Content-Type", "application/json")
        client.Headers.Add("Accept", "application/json")
        if token:
            client.Headers.Add("Authorization", "Bearer " + token)
        body = json.dumps(data)
        response = client.UploadString(url, "POST", body)
        return json.loads(response)
    except System.Net.WebException as e:
        resp = e.Response
        detail = ""
        if resp:
            stream = resp.GetResponseStream()
            reader = System.IO.StreamReader(stream)
            detail = reader.ReadToEnd()
        raise RuntimeError("HTTP error: {0} - {1}".format(str(e.Message), detail[:300]))
    except Exception as e:
        raise RuntimeError(str(e))


def get_json(url, token=None, timeout_ms=None):
    try:
        client = _web_client(timeout_ms)
        client.Headers.Add("Accept", "application/json")
        if token:
            client.Headers.Add("Authorization", "Bearer " + token)
        response = client.DownloadString(url)
        return json.loads(response)
    except System.Net.WebException as e:
        resp = e.Response
        detail = ""
        if resp:
            stream = resp.GetResponseStream()
            reader = System.IO.StreamReader(stream)
            detail = reader.ReadToEnd()
        raise RuntimeError("HTTP error: {0} - {1}".format(str(e.Message), detail[:300]))
    except Exception as e:
        raise RuntimeError(str(e))


def get_binary(url, token=None, timeout_ms=None):
    try:
        client = _web_client(timeout_ms)
        if token:
            client.Headers.Add("Authorization", "Bearer " + token)
        return client.DownloadData(url)
    except System.Net.WebException as e:
        resp = e.Response
        status = int(resp.StatusCode) if resp else 0
        if status == 404:
            raise RuntimeError(
                "no geometry snapshot for this bowl yet - open it in the web "
                "app and hit Save once")
        detail = ""
        if resp:
            stream = resp.GetResponseStream()
            reader = System.IO.StreamReader(stream)
            detail = reader.ReadToEnd()
        raise RuntimeError("HTTP error: {0} - {1}".format(str(e.Message), detail[:300]))
    except Exception as e:
        raise RuntimeError(str(e))


def gunzip_bytes(data):
    """.NET byte[] (gzip) -> .NET byte[] (decompressed)."""
    ms_in = System.IO.MemoryStream(data)
    gz = System.IO.Compression.GZipStream(ms_in, System.IO.Compression.CompressionMode.Decompress)
    ms_out = System.IO.MemoryStream()
    buffer = System.Array.CreateInstance(System.Byte, 8192)
    while True:
        read = gz.Read(buffer, 0, buffer.Length)
        if read <= 0:
            break
        ms_out.Write(buffer, 0, read)
    gz.Close()
    return ms_out.ToArray()


def login(email, password, timeout_ms=None):
    resp = post_json(BASE_URL + "/api/v1/auth/login",
                      {"email": email, "password": password}, timeout_ms=timeout_ms)
    token = resp.get("token")
    if not token:
        raise RuntimeError("login succeeded but no token returned")
    return token


def create_aline(token, name, is_closed, pts):
    body = {
        "name": name,
        "closed": is_closed,
        "points": [{"x": x, "y": y} for x, y in pts],
    }
    return post_json(BASE_URL + "/api/v1/alines", body, token=token)


def create_void(token, name, loops, z1, z2):
    body = {
        "name": name,
        "loops": [[{"x": x, "y": y} for x, y in loop] for loop in loops],
        "z1": z1,
        "z2": z2,
    }
    return post_json(BASE_URL + "/api/v1/voids", body, token=token)


def create_aisle_group(token, name, paths):
    body = {
        "name": name,
        "paths": [[{"x": x, "y": y} for x, y in path] for path in paths],
    }
    return post_json(BASE_URL + "/api/v1/aisles", body, token=token)


def list_bowls(token):
    return get_json(BASE_URL + "/api/v1/bowls", token=token, timeout_ms=180000)


def download_bowl_export(token, bowl_id):
    return get_binary(BASE_URL + "/api/v1/bowls/{0}/export".format(bowl_id),
                       token=token, timeout_ms=180000)


def import_3dm_bytes(data, bowl_name):
    """Merge a .3dm byte blob into the active doc. Returns (added, deleted, unit_note)."""
    if len(data) >= 2 and data[0] == 0x1f and data[1] == 0x8b:
        data = gunzip_bytes(data)

    f3dm = Rhino.FileIO.File3dm.FromByteArray(data)
    if f3dm is None:
        raise RuntimeError("server returned bytes that are not a valid .3dm file")

    doc = sc.doc
    scale = Rhino.RhinoMath.UnitScale(f3dm.Settings.ModelUnitSystem, doc.ModelUnitSystem)
    xf = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, scale)

    # BOWL-SCOPED LAYERS: every bowl exports identically-named layers
    # ("Sweep 1 A-Line", "Section Annotations", ...), so nest each pull
    # under a top-level layer named after the bowl. Re-pulling the SAME
    # bowl finds and reuses its own parent layer (update in place);
    # pulling a DIFFERENT bowl gets its own parent and never touches
    # another bowl's sublayers even though the leaf names collide.
    parent_id = System.Guid.Empty
    for lyr in doc.Layers:
        if lyr.Name == bowl_name and lyr.ParentLayerId == System.Guid.Empty:
            parent_id = lyr.Id
            break
    if parent_id == System.Guid.Empty:
        bowl_layer = Rhino.DocObjects.Layer()
        bowl_layer.Name = bowl_name
        bowl_idx = doc.Layers.Add(bowl_layer)
        parent_id = doc.Layers[bowl_idx].Id

    # REPLACE-BY-LAYER: delete existing objects on any doc sublayer (under
    # this bowl's parent layer) whose name matches an incoming layer, so
    # re-pulling the same bowl updates in place.
    deleted = 0
    layer_map = {}
    for layer in f3dm.AllLayers:
        existing = None
        for lyr in doc.Layers:
            if lyr.Name == layer.Name and lyr.ParentLayerId == parent_id:
                existing = lyr
                break
        if existing is not None:
            for obj in (doc.Objects.FindByLayer(existing) or []):
                if doc.Objects.Delete(obj, True):
                    deleted += 1
            layer_map[layer.Index] = existing.Index
        else:
            new_layer = Rhino.DocObjects.Layer()
            new_layer.Name = layer.Name
            new_layer.Color = layer.Color
            new_layer.ParentLayerId = parent_id
            layer_map[layer.Index] = doc.Layers.Add(new_layer)

    # SEAT BLOCKS: upsert instance definitions by name so a re-pull redefines
    # the block in place; remember file-idef-id -> doc-idef-index for the
    # instance references below. Definition backing geometry is shared
    # across every bowl that uses that seat width (upsert-by-name), so it
    # doesn't belong under any one bowl's layer tree - host it on its own
    # "Seat Blocks" parent layer (one sublayer per distinct width, e.g.
    # "Seat 24inch") instead, so toggling a bowl's layers can never hide
    # another bowl's seats. Placed seat INSTANCES further below are
    # unaffected by this and stay scoped to their own bowl as before.
    seat_blocks_parent_id = System.Guid.Empty
    for lyr in doc.Layers:
        if lyr.Name == "Seat Blocks" and lyr.ParentLayerId == System.Guid.Empty:
            seat_blocks_parent_id = lyr.Id
            break
    if seat_blocks_parent_id == System.Guid.Empty:
        seat_blocks_layer = Rhino.DocObjects.Layer()
        seat_blocks_layer.Name = "Seat Blocks"
        seat_blocks_idx = doc.Layers.Add(seat_blocks_layer)
        seat_blocks_parent_id = doc.Layers[seat_blocks_idx].Id

    file_objs_by_id = {}
    for obj in f3dm.Objects:
        file_objs_by_id[obj.Attributes.ObjectId] = obj

    idef_index_map = {}
    for idef in f3dm.AllInstanceDefinitions:
        seat_layer_index = None
        for lyr in doc.Layers:
            if lyr.Name == idef.Name and lyr.ParentLayerId == seat_blocks_parent_id:
                seat_layer_index = lyr.Index
                break
        if seat_layer_index is None:
            seat_layer = Rhino.DocObjects.Layer()
            seat_layer.Name = idef.Name
            seat_layer.ParentLayerId = seat_blocks_parent_id
            seat_layer_index = doc.Layers.Add(seat_layer)

        geoms = []
        attrs_list = []
        for gid in idef.GetObjectIds():
            fobj = file_objs_by_id.get(gid)
            if fobj is None or fobj.Geometry is None:
                continue
            g = fobj.Geometry.Duplicate()
            if abs(scale - 1.0) > 1e-12:
                g.Transform(xf)
            geoms.append(g)
            a = fobj.Attributes.Duplicate()
            a.LayerIndex = seat_layer_index
            attrs_list.append(a)
        if not geoms:
            continue
        existing = doc.InstanceDefinitions.Find(idef.Name)
        if existing is not None:
            doc.InstanceDefinitions.ModifyGeometry(existing.Index, geoms, attrs_list)
            idef_index_map[idef.Id] = existing.Index
        else:
            new_idx = doc.InstanceDefinitions.Add(
                idef.Name, idef.Description or "", Rhino.Geometry.Point3d.Origin,
                geoms, attrs_list)
            if new_idx >= 0:
                idef_index_map[idef.Id] = new_idx

    added = 0
    skipped_refs = 0
    for obj in f3dm.Objects:
        geom = obj.Geometry
        if geom is None:
            continue
        # Definition geometry lives in the object table too - skip it, the
        # doc definitions above already carry it (else seats duplicate at origin).
        if obj.Attributes.Mode == Rhino.DocObjects.ObjectMode.InstanceDefinitionObject:
            continue

        attrs = obj.Attributes.Duplicate()
        attrs.LayerIndex = layer_map.get(attrs.LayerIndex, doc.Layers.CurrentLayerIndex)

        if isinstance(geom, Rhino.Geometry.InstanceReferenceGeometry):
            doc_idx = idef_index_map.get(geom.ParentIdefId)
            if doc_idx is None:
                skipped_refs += 1
                continue
            ref_xf = geom.Xform
            if abs(scale - 1.0) > 1e-12:
                # Conjugate: doc-unit block, placement scaled to doc units.
                inv = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, 1.0 / scale)
                ref_xf = xf * ref_xf * inv
            if doc.Objects.AddInstanceObject(doc_idx, ref_xf, attrs) != System.Guid.Empty:
                added += 1
            continue

        geom = geom.Duplicate()
        if abs(scale - 1.0) > 1e-12:
            geom.Transform(xf)
        if doc.Objects.Add(geom, attrs) != System.Guid.Empty:
            added += 1

    f3dm.Dispose()
    doc.Views.Redraw()
    if skipped_refs:
        print("WARNING: {0} block instances skipped (missing definition)".format(skipped_refs))
    unit_note = "" if abs(scale - 1.0) < 1e-12 else " (scaled x{0:g} to match doc units)".format(scale)
    return added, deleted, unit_note


def bowl_label(b):
    count = b.get("sectionCount", 0)
    plural = "" if count == 1 else "s"
    updated = (b.get("updatedAt") or "?")[:16].replace("T", " ")
    return "{0}   ({1} sweep{2}, updated {3})".format(b.get("name"), count, plural, updated)


# ── UI helpers ───────────────────────────────────────────────────────────────

def make_label(text, muted=False):
    l = forms.Label()
    l.Text = text
    l.TextColor = COL_MUTED if muted else COL_TEXT
    return l


def style_textbox(tb):
    tb.BackgroundColor = COL_SURFACE
    tb.TextColor = COL_TEXT
    return tb


def style_button(btn, accent=False):
    btn.BackgroundColor = COL_ACCENT if accent else COL_SURFACE
    btn.TextColor = COL_TEXT
    return btn


def make_tab_layout():
    l = forms.DynamicLayout()
    l.Padding = drawing.Padding(14)
    l.DefaultSpacing = drawing.Size(6, 6)
    l.BackgroundColor = COL_BG
    return l


# ── Rotating preview ─────────────────────────────────────────────────────────

class CurvePreview(forms.Drawable):
    """Draws a slowly rotating wireframe preview of a polyline."""

    def __init__(self):
        self.Size = drawing.Size(340, 160)
        self.BackgroundColor = COL_SURFACE
        self.points = None  # list of (x, y, z) - live, may update each tick
        self.angle = 0.0
        self.fixed_extent = None
        self.live_points_fn = None  # optional callable returning fresh points
        self.Paint += self.on_paint

        self.timer = forms.UITimer()
        self.timer.Interval = 0.03
        self.timer.Elapsed += self.on_tick
        self.timer.Start()

    def set_points(self, points):
        """Set the baseline points and (re)compute the fixed display scale."""
        self.points = points
        self.fixed_extent = None
        if points:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            zs = [p[2] for p in points]
            cx = (max(xs) + min(xs)) / 2.0
            cy = (max(ys) + min(ys)) / 2.0
            cz = (max(zs) + min(zs)) / 2.0
            # XY radius (rotation-invariant) plus z half-height
            radius = max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y, z in points)
            half_z = (max(zs) - min(zs)) / 2.0
            iso_cos = 0.8660254037844387  # cos(30deg)
            iso_sin = 0.5                 # sin(30deg)
            self.fixed_extent = max(2 * radius * iso_cos, 2 * radius * iso_sin + half_z, 1e-6)
        self.Invalidate()

    def update_live_points(self, points):
        """Update the displayed geometry without changing the fixed scale."""
        self.points = points
        self.Invalidate()

    def on_tick(self, sender, e):
        if self.live_points_fn:
            self.update_live_points(self.live_points_fn())
        if self.points:
            self.angle += 0.02
            self.Invalidate()

    def on_paint(self, sender, e):
        g = e.Graphics
        w, h = self.Size.Width, self.Size.Height
        g.FillRectangle(COL_SURFACE, drawing.RectangleF(0, 0, w, h))

        if not self.points or len(self.points) < 2:
            return

        # Center the geometry around its bounding-box midpoint
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        zs = [p[2] for p in self.points]
        cx = (max(xs) + min(xs)) / 2.0
        cy = (max(ys) + min(ys)) / 2.0
        cz = (max(zs) + min(zs)) / 2.0

        import math
        cos_a = math.cos(self.angle)
        sin_a = math.sin(self.angle)

        # Isometric projection angles
        iso_cos = math.cos(math.radians(30))
        iso_sin = math.sin(math.radians(30))

        screen_pts = []
        for x, y, z in self.points:
            x0, y0, z0 = x - cx, y - cy, z - cz
            # Rotate around the vertical (Z) axis
            rx = x0 * cos_a - y0 * sin_a
            ry = x0 * sin_a + y0 * cos_a
            rz = z0
            # Isometric projection
            sx = (rx - ry) * iso_cos
            sy = (rx + ry) * iso_sin - rz
            screen_pts.append((sx, sy))

        margin = 1.5
        scale = (min(w, h) / 2.0) * margin / self.fixed_extent

        poly = []
        for rx, ry in screen_pts:
            sx = w / 2.0 + rx * scale
            sy = h / 2.0 - ry * scale
            poly.append(drawing.PointF(sx, sy))

        for i in range(len(poly) - 1):
            g.DrawLine(COL_ACCENT, poly[i], poly[i + 1])
        for p in poly:
            g.FillEllipse(COL_ACCENT, p.X - 2, p.Y - 2, 4, 4)


# ── Dialog ───────────────────────────────────────────────────────────────────

class BowlConnectorDialog(forms.Form):

    def __init__(self):
        self.selected_curve_ids = []
        self.captured_pts = None  # snapshot of (x, y, z) points at time of selection
        self.void_curve_ids = []
        self.aisle_curve_ids = []
        self._bowls = []
        self._pull_token = None

        self.Title = "Seating Bowl Generator - Rhino Connector"
        self.Resizable = False
        self.AutoSize = True
        self.BackgroundColor = COL_BG

        # ── Shared login fields ──────────────────────────────────────────────
        self.txt_email = style_textbox(forms.TextBox())
        self.txt_email.PlaceholderText = "user@example.com"
        self.txt_email.Width = 340

        self.txt_password = style_textbox(forms.PasswordBox())
        self.txt_password.Width = 340
        self.txt_password.Height = self.txt_email.Height if self.txt_email.Height > 0 else 22

        self.btn_close = style_button(forms.Button())
        self.btn_close.Text = "Close"
        self.btn_close.MinimumSize = drawing.Size(100, 30)
        self.btn_close.Size = drawing.Size(100, 30)
        self.btn_close.Click += self.on_close

        # Eto's native TabControl/TabPage headers use OS-themed text that
        # can't be recolored (they rendered unreadable-dark on this dialog's
        # background) — use a plain button row instead, styled like every
        # other button here, so the tab labels are always legible.
        self._tab_names = ["A-Line", "Void", "Aisle", "Pull"]
        self._tab_layouts = {
            "A-Line": self._build_aline_tab(),
            "Void": self._build_void_tab(),
            "Aisle": self._build_aisle_tab(),
            "Pull": self._build_pull_tab(),
        }
        self._tab_buttons = {}

        tab_bar = forms.DynamicLayout()
        tab_bar.BackgroundColor = COL_BG
        tab_bar.Spacing = drawing.Size(4, 0)
        tab_cells = []
        for name in self._tab_names:
            b = style_button(forms.Button())
            b.Text = name
            b.Width = 80
            b.Click += self._make_tab_click_handler(name)
            self._tab_buttons[name] = b
            tab_cells.append(b)
        tab_bar.AddRow(*tab_cells)

        self.tab_content_panel = forms.Panel()
        self.tab_content_panel.BackgroundColor = COL_BG

        # ── Top-level layout ─────────────────────────────────────────────────
        layout = forms.DynamicLayout()
        layout.Padding = drawing.Padding(20)
        layout.Spacing = drawing.Size(0, 10)
        layout.DefaultSpacing = drawing.Size(6, 6)
        layout.BackgroundColor = COL_BG

        layout.AddRow(make_label("Email"))
        layout.AddRow(self.txt_email)
        layout.AddRow(make_label("Password"))
        layout.AddRow(self.txt_password)
        layout.AddRow(tab_bar)
        layout.AddRow(self.tab_content_panel)

        btn_panel = forms.Panel()
        btn_panel.BackgroundColor = COL_BG
        btn_panel.Height = 32
        btn_panel.Content = self.btn_close
        layout.AddRow(btn_panel)

        self.Content = layout
        self._select_tab("A-Line")

    def _make_tab_click_handler(self, name):
        def handler(sender, e):
            self._select_tab(name)
        return handler

    def _select_tab(self, name):
        self.tab_content_panel.Content = self._tab_layouts[name]
        for n, b in self._tab_buttons.items():
            style_button(b, accent=(n == name))

    # ── A-Line tab ───────────────────────────────────────────────────────────

    def _build_aline_tab(self):
        self.txt_aline_name = style_textbox(forms.TextBox())
        self.txt_aline_name.PlaceholderText = "A-Line name"
        self.txt_aline_name.Width = 340

        self.chk_closed = forms.CheckBox()
        self.chk_closed.Text = ""
        self.chk_closed.Checked = True
        self.lbl_closed = make_label("Closed polyline")

        self.btn_select_aline = style_button(forms.Button())
        self.btn_select_aline.Text = "Select Curve in Rhino"
        self.btn_select_aline.Width = 220
        self.btn_select_aline.Click += self.on_select_aline_curve

        self.lbl_aline_curve_status = make_label("No curve selected.", muted=True)

        self.preview = CurvePreview()

        self.lbl_status_aline = forms.Label()
        self.lbl_status_aline.Text = ""
        self.lbl_status_aline.Width = 340
        self.lbl_status_aline.TextColor = COL_ACCENT

        self.btn_send_aline = style_button(forms.Button(), accent=True)
        self.btn_send_aline.Text = "Send A-Line"
        self.btn_send_aline.MinimumSize = drawing.Size(160, 30)
        self.btn_send_aline.Size = drawing.Size(160, 30)
        self.btn_send_aline.Click += self.on_send_aline

        layout = make_tab_layout()
        layout.AddRow(make_label("A-Line Name"))
        layout.AddRow(self.txt_aline_name)
        chk_row = forms.DynamicLayout()
        chk_row.BackgroundColor = COL_BG
        chk_row.Spacing = drawing.Size(6, 0)
        chk_row.AddRow(self.chk_closed, self.lbl_closed)
        layout.AddRow(chk_row)
        layout.AddRow(self.btn_select_aline)
        layout.AddRow(self.lbl_aline_curve_status)
        layout.AddRow(self.preview)
        layout.AddRow(self.lbl_status_aline)
        layout.AddRow(self.btn_send_aline)

        return layout

    def on_select_aline_curve(self, sender, e):
        self.Visible = False
        try:
            ids = rs.GetObjects(
                message="Select a polyline curve to send",
                filter=rs.filter.curve,
                preselect=True,
            )
            if ids:
                self.selected_curve_ids = list(ids)
                self.lbl_aline_curve_status.Text = "{0} curve(s) selected.".format(len(ids))

                obj = sc.doc.Objects.FindId(self.selected_curve_ids[0])
                curve = coerce_curve(obj) if obj else None
                preview_pts = get_preview_points(curve) if curve else None
                self.captured_pts = preview_pts
                self.preview.set_points(preview_pts)

                live_id = self.selected_curve_ids[0]

                def fetch_live_points():
                    obj = sc.doc.Objects.FindId(live_id)
                    curve = coerce_curve(obj) if obj else None
                    return get_preview_points(curve) if curve else None

                self.preview.live_points_fn = fetch_live_points
            else:
                self.selected_curve_ids = []
                self.captured_pts = None
                self.lbl_aline_curve_status.Text = "No curve selected."
                self.preview.live_points_fn = None
                self.preview.set_points(None)
        finally:
            self.Visible = True

    def on_send_aline(self, sender, e):
        email     = self.txt_email.Text.strip()
        password  = self.txt_password.Text
        name      = self.txt_aline_name.Text.strip()
        is_closed = bool(self.chk_closed.Checked)

        if not email or not password:
            self.lbl_status_aline.Text = "Email and password are required."
            return
        if not name:
            self.lbl_status_aline.Text = "Please enter an A-Line name."
            return
        if not self.captured_pts:
            self.lbl_status_aline.Text = "No curve selected."
            return

        pts, err = extract_2d_points(self.captured_pts, is_closed)
        if err:
            self.lbl_status_aline.Text = err
            return

        self.lbl_status_aline.Text = "Sending..."
        self.btn_send_aline.Enabled = False

        captured = {
            "email": email, "password": password,
            "name": name, "is_closed": is_closed, "pts": pts,
        }

        def do_send():
            try:
                token = login(captured["email"], captured["password"])
                aline = create_aline(token, captured["name"], captured["is_closed"], captured["pts"])
                msg = "Created: id={0}  name={1}  points={2}".format(
                    aline.get("id"), aline.get("name"), len(captured["pts"])
                )
            except RuntimeError as ex:
                msg = "Error: " + str(ex)

            def update_ui():
                self.lbl_status_aline.Text = msg
                self.btn_send_aline.Enabled = True

            Rhino.RhinoApp.InvokeOnUiThread(System.Action(update_ui))

        t = threading.Thread(target=do_send)
        t.daemon = True
        t.start()

    # ── Void tab ─────────────────────────────────────────────────────────────

    def _build_void_tab(self):
        self.txt_void_name = style_textbox(forms.TextBox())
        self.txt_void_name.PlaceholderText = "Void name"
        self.txt_void_name.Width = 340

        self.txt_void_z1 = style_textbox(forms.TextBox())
        self.txt_void_z1.PlaceholderText = "Z1 (bottom elevation)"
        self.txt_void_z1.Width = 340

        self.txt_void_z2 = style_textbox(forms.TextBox())
        self.txt_void_z2.PlaceholderText = "Z2 (top elevation)"
        self.txt_void_z2.Width = 340

        self.btn_select_voids = style_button(forms.Button())
        self.btn_select_voids.Text = "Select Loops in Rhino"
        self.btn_select_voids.Width = 220
        self.btn_select_voids.Click += self.on_select_voids

        self.lbl_void_status = make_label("No loops selected.", muted=True)

        self.lbl_status_void = forms.Label()
        self.lbl_status_void.Text = ""
        self.lbl_status_void.Width = 340
        self.lbl_status_void.TextColor = COL_ACCENT

        self.btn_send_void = style_button(forms.Button(), accent=True)
        self.btn_send_void.Text = "Send Void"
        self.btn_send_void.MinimumSize = drawing.Size(160, 30)
        self.btn_send_void.Size = drawing.Size(160, 30)
        self.btn_send_void.Click += self.on_send_void

        layout = make_tab_layout()
        layout.AddRow(make_label("Void Name"))
        layout.AddRow(self.txt_void_name)
        layout.AddRow(make_label("Z1"))
        layout.AddRow(self.txt_void_z1)
        layout.AddRow(make_label("Z2"))
        layout.AddRow(self.txt_void_z2)
        layout.AddRow(self.btn_select_voids)
        layout.AddRow(self.lbl_void_status)
        layout.AddRow(self.lbl_status_void)
        layout.AddRow(self.btn_send_void)

        return layout

    def on_select_voids(self, sender, e):
        self.Visible = False
        try:
            ids = rs.GetObjects(
                message="Select one or more closed loops for the void",
                filter=rs.filter.curve,
                preselect=True,
            )
            if ids:
                self.void_curve_ids = list(ids)
                self.lbl_void_status.Text = "{0} loop(s) selected.".format(len(ids))
            else:
                self.void_curve_ids = []
                self.lbl_void_status.Text = "No loops selected."
        finally:
            self.Visible = True

    def on_send_void(self, sender, e):
        email    = self.txt_email.Text.strip()
        password = self.txt_password.Text
        name     = self.txt_void_name.Text.strip()
        z1_text  = self.txt_void_z1.Text.strip()
        z2_text  = self.txt_void_z2.Text.strip()

        if not email or not password:
            self.lbl_status_void.Text = "Email and password are required."
            return
        if not name:
            self.lbl_status_void.Text = "Please enter a void name."
            return
        if not self.void_curve_ids:
            self.lbl_status_void.Text = "No loops selected."
            return
        try:
            z1 = float(z1_text)
            z2 = float(z2_text)
        except ValueError:
            self.lbl_status_void.Text = "Z1 and Z2 must be numbers."
            return

        try:
            loops = []
            for cid in self.void_curve_ids:
                obj = sc.doc.Objects.FindId(cid)
                curve = coerce_curve(obj) if obj else None
                if curve is None:
                    raise RuntimeError("could not read one of the selected loops")
                loops.append(closed_curve_points(curve))
        except RuntimeError as ex:
            self.lbl_status_void.Text = "Error: " + str(ex)
            return

        self.lbl_status_void.Text = "Sending..."
        self.btn_send_void.Enabled = False

        captured = {
            "email": email, "password": password,
            "name": name, "loops": loops, "z1": z1, "z2": z2,
        }

        def do_send():
            try:
                token = login(captured["email"], captured["password"])
                v = create_void(token, captured["name"], captured["loops"],
                                 captured["z1"], captured["z2"])
                msg = "Created: id={0}  name={1}  loops={2}  z {3}..{4}".format(
                    v.get("id"), v.get("name"), len(captured["loops"]),
                    v.get("z1"), v.get("z2"))
            except RuntimeError as ex:
                msg = "Error: " + str(ex)

            def update_ui():
                self.lbl_status_void.Text = msg
                self.btn_send_void.Enabled = True

            Rhino.RhinoApp.InvokeOnUiThread(System.Action(update_ui))

        t = threading.Thread(target=do_send)
        t.daemon = True
        t.start()

    # ── Aisle tab ────────────────────────────────────────────────────────────

    def _build_aisle_tab(self):
        self.txt_aisle_name = style_textbox(forms.TextBox())
        self.txt_aisle_name.PlaceholderText = "Aisle group name"
        self.txt_aisle_name.Width = 340

        self.btn_select_aisles = style_button(forms.Button())
        self.btn_select_aisles.Text = "Select Aisle Paths in Rhino"
        self.btn_select_aisles.Width = 220
        self.btn_select_aisles.Click += self.on_select_aisles

        self.lbl_aisle_status = make_label("No paths selected.", muted=True)

        self.lbl_status_aisle = forms.Label()
        self.lbl_status_aisle.Text = ""
        self.lbl_status_aisle.Width = 340
        self.lbl_status_aisle.TextColor = COL_ACCENT

        self.btn_send_aisle = style_button(forms.Button(), accent=True)
        self.btn_send_aisle.Text = "Send Aisle Group"
        self.btn_send_aisle.MinimumSize = drawing.Size(160, 30)
        self.btn_send_aisle.Size = drawing.Size(160, 30)
        self.btn_send_aisle.Click += self.on_send_aisle

        layout = make_tab_layout()
        layout.AddRow(make_label("Aisle Group Name"))
        layout.AddRow(self.txt_aisle_name)
        layout.AddRow(self.btn_select_aisles)
        layout.AddRow(self.lbl_aisle_status)
        layout.AddRow(self.lbl_status_aisle)
        layout.AddRow(self.btn_send_aisle)

        return layout

    def on_select_aisles(self, sender, e):
        self.Visible = False
        try:
            ids = rs.GetObjects(
                message="Select one or more open aisle centerline curves",
                filter=rs.filter.curve,
                preselect=True,
            )
            if ids:
                self.aisle_curve_ids = list(ids)
                self.lbl_aisle_status.Text = "{0} path(s) selected.".format(len(ids))
            else:
                self.aisle_curve_ids = []
                self.lbl_aisle_status.Text = "No paths selected."
        finally:
            self.Visible = True

    def on_send_aisle(self, sender, e):
        email    = self.txt_email.Text.strip()
        password = self.txt_password.Text
        name     = self.txt_aisle_name.Text.strip()

        if not email or not password:
            self.lbl_status_aisle.Text = "Email and password are required."
            return
        if not name:
            self.lbl_status_aisle.Text = "Please enter an aisle group name."
            return
        if not self.aisle_curve_ids:
            self.lbl_status_aisle.Text = "No paths selected."
            return

        try:
            paths = []
            for i, cid in enumerate(self.aisle_curve_ids):
                obj = sc.doc.Objects.FindId(cid)
                curve = coerce_curve(obj) if obj else None
                if curve is None:
                    raise RuntimeError("could not read one of the selected paths")
                paths.append(open_curve_points(curve, i))
        except RuntimeError as ex:
            self.lbl_status_aisle.Text = "Error: " + str(ex)
            return

        self.lbl_status_aisle.Text = "Sending..."
        self.btn_send_aisle.Enabled = False

        captured = {"email": email, "password": password, "name": name, "paths": paths}

        def do_send():
            try:
                token = login(captured["email"], captured["password"])
                group = create_aisle_group(token, captured["name"], captured["paths"])
                msg = "Created: id={0}  name={1}  paths={2}".format(
                    group.get("id"), group.get("name"), len(captured["paths"]))
            except RuntimeError as ex:
                msg = "Error: " + str(ex)

            def update_ui():
                self.lbl_status_aisle.Text = msg
                self.btn_send_aisle.Enabled = True

            Rhino.RhinoApp.InvokeOnUiThread(System.Action(update_ui))

        t = threading.Thread(target=do_send)
        t.daemon = True
        t.start()

    # ── Pull tab ─────────────────────────────────────────────────────────────

    def _build_pull_tab(self):
        self.btn_list_bowls = style_button(forms.Button())
        self.btn_list_bowls.Text = "List My Bowls"
        self.btn_list_bowls.Width = 220
        self.btn_list_bowls.Click += self.on_list_bowls

        self.lst_bowls = forms.ListBox()
        self.lst_bowls.Size = drawing.Size(340, 120)
        self.lst_bowls.BackgroundColor = COL_SURFACE
        self.lst_bowls.TextColor = COL_TEXT

        self.lbl_status_pull = forms.Label()
        self.lbl_status_pull.Text = "Log in above, then List My Bowls."
        self.lbl_status_pull.Width = 340
        self.lbl_status_pull.TextColor = COL_ACCENT

        self.btn_pull_selected = style_button(forms.Button(), accent=True)
        self.btn_pull_selected.Text = "Pull Selected Bowl"
        self.btn_pull_selected.MinimumSize = drawing.Size(160, 30)
        self.btn_pull_selected.Size = drawing.Size(160, 30)
        self.btn_pull_selected.Click += self.on_pull_selected

        layout = make_tab_layout()
        layout.AddRow(self.btn_list_bowls)
        layout.AddRow(self.lst_bowls)
        layout.AddRow(self.lbl_status_pull)
        layout.AddRow(self.btn_pull_selected)

        return layout

    def on_list_bowls(self, sender, e):
        email    = self.txt_email.Text.strip()
        password = self.txt_password.Text

        if not email or not password:
            self.lbl_status_pull.Text = "Email and password are required."
            return

        self.lbl_status_pull.Text = "Logging in (may take up to a minute on a cold start)..."
        self.btn_list_bowls.Enabled = False

        captured = {"email": email, "password": password}

        def do_list():
            token = None
            bowls = []
            err = None
            try:
                token = login(captured["email"], captured["password"], timeout_ms=180000)
                bowls = list_bowls(token)
            except RuntimeError as ex:
                err = str(ex)

            def update_ui():
                self.btn_list_bowls.Enabled = True
                if err:
                    self.lbl_status_pull.Text = "Error: " + err
                    return
                self._pull_token = token
                self._bowls = bowls
                self.lst_bowls.Items.Clear()
                if not bowls:
                    self.lbl_status_pull.Text = "No saved bowls on this account - save one in the web app first."
                    return
                for b in bowls:
                    self.lst_bowls.Items.Add(bowl_label(b))
                self.lbl_status_pull.Text = "{0} bowl(s) loaded - pick one and Pull.".format(len(bowls))

            Rhino.RhinoApp.InvokeOnUiThread(System.Action(update_ui))

        t = threading.Thread(target=do_list)
        t.daemon = True
        t.start()

    def on_pull_selected(self, sender, e):
        if not self._pull_token:
            self.lbl_status_pull.Text = "List bowls first."
            return
        idx = self.lst_bowls.SelectedIndex
        if idx is None or idx < 0 or idx >= len(self._bowls):
            self.lbl_status_pull.Text = "Select a bowl from the list."
            return
        bowl = self._bowls[idx]
        token = self._pull_token

        self.lbl_status_pull.Text = "Downloading '{0}' ...".format(bowl.get("name"))
        self.btn_pull_selected.Enabled = False

        def do_pull():
            data = None
            err = None
            try:
                data = download_bowl_export(token, bowl["id"])
            except RuntimeError as ex:
                err = str(ex)

            def finish():
                self.btn_pull_selected.Enabled = True
                if err:
                    self.lbl_status_pull.Text = "Error: " + err
                    return
                try:
                    added, deleted, unit_note = import_3dm_bytes(data, bowl.get("name") or "Pulled Bowl")
                    self.lbl_status_pull.Text = "OK - '{0}': {1} added, {2} replaced{3}.".format(
                        bowl.get("name"), added, deleted, unit_note)
                except Exception as ex:
                    self.lbl_status_pull.Text = "Error: " + str(ex)

            Rhino.RhinoApp.InvokeOnUiThread(System.Action(finish))

        t = threading.Thread(target=do_pull)
        t.daemon = True
        t.start()

    # ── Shared ───────────────────────────────────────────────────────────────

    def on_close(self, sender, e):
        self.preview.timer.Stop()
        self.Close()


def main():
    dialog = BowlConnectorDialog()
    main_win = Rhino.UI.RhinoEtoApp.MainWindow
    dialog.Owner = main_win
    dialog.Location = drawing.Point(
        main_win.Location.X + 60,
        main_win.Location.Y + 60,
    )
    dialog.Show()


if __name__ == "__main__":
    main()
