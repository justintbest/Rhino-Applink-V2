# -*- coding: utf-8 -*-
# rhino_api_sender_2.py
# Popup panel to build and send an A-Line to the bowl backend.

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


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def post_json(url, data, token=None):
    try:
        client = System.Net.WebClient()
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
        raise RuntimeError("HTTP error: {0} — {1}".format(str(e.Message), detail[:300]))
    except Exception as e:
        raise RuntimeError(str(e))


def login(email, password):
    resp = post_json(BASE_URL + "/api/v1/auth/login", {"email": email, "password": password})
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


# ── Rotating preview ─────────────────────────────────────────────────────────

class CurvePreview(forms.Drawable):
    """Draws a slowly rotating wireframe preview of a polyline."""

    def __init__(self):
        self.Size = drawing.Size(340, 160)
        self.BackgroundColor = COL_SURFACE
        self.points = None  # list of (x, y, z) — live, may update each tick
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

class ALineSenderDialog(forms.Form):

    def __init__(self):
        self.selected_curve_ids = []
        self.captured_pts = None  # snapshot of (x, y, z) points at time of selection

        self.Title = "Seating Bowl Generator - Rhino Connector"
        self.Resizable = False
        self.AutoSize = True
        self.BackgroundColor = COL_BG

        # ── Fields ──────────────────────────────────────────────────────────
        self.txt_email = style_textbox(forms.TextBox())
        self.txt_email.PlaceholderText = "user@example.com"
        self.txt_email.Width = 340

        self.txt_password = style_textbox(forms.PasswordBox())
        self.txt_password.Width = 340
        self.txt_password.Height = self.txt_email.Height if self.txt_email.Height > 0 else 22

        self.txt_name = style_textbox(forms.TextBox())
        self.txt_name.PlaceholderText = "A-Line name"
        self.txt_name.Width = 340

        self.chk_closed = forms.CheckBox()
        self.chk_closed.Text = ""
        self.chk_closed.Checked = True
        self.lbl_closed = make_label("Closed polyline")

        self.btn_select = style_button(forms.Button())
        self.btn_select.Text = "Select Curve in Rhino"
        self.btn_select.Width = 220
        self.btn_select.Click += self.on_select_curve

        self.lbl_curve_status = make_label("No curve selected.", muted=True)

        self.preview = CurvePreview()

        self.lbl_status = forms.Label()
        self.lbl_status.Text = ""
        self.lbl_status.Width = 340
        self.lbl_status.TextColor = COL_ACCENT

        self.btn_send = style_button(forms.Button(), accent=True)
        self.btn_send.Text = "Send A-Line"
        self.btn_send.MinimumSize = drawing.Size(160, 30)
        self.btn_send.Size = drawing.Size(160, 30)
        self.btn_send.Click += self.on_send

        self.btn_close = style_button(forms.Button())
        self.btn_close.Text = "Close"
        self.btn_close.MinimumSize = drawing.Size(100, 30)
        self.btn_close.Size = drawing.Size(100, 30)
        self.btn_close.Click += self.on_close

        # ── Layout ──────────────────────────────────────────────────────────
        layout = forms.DynamicLayout()
        layout.Padding = drawing.Padding(20)
        layout.Spacing = drawing.Size(0, 10)
        layout.DefaultSpacing = drawing.Size(6, 6)
        layout.BackgroundColor = COL_BG

        layout.AddRow(make_label("Email"))
        layout.AddRow(self.txt_email)
        layout.AddRow(make_label("Password"))
        layout.AddRow(self.txt_password)
        layout.AddRow(make_label("A-Line Name"))
        layout.AddRow(self.txt_name)
        chk_row = forms.DynamicLayout()
        chk_row.BackgroundColor = COL_BG
        chk_row.Spacing = drawing.Size(6, 0)
        chk_row.AddRow(self.chk_closed, self.lbl_closed)
        layout.AddRow(chk_row)
        layout.AddRow(self.btn_select)
        layout.AddRow(self.lbl_curve_status)
        layout.AddRow(self.preview)
        layout.AddRow(self.lbl_status)

        btn_row = forms.TableLayout()
        btn_row.Spacing = drawing.Size(10, 0)
        btn_row.BackgroundColor = COL_BG
        btn_row.Rows.Add(forms.TableRow(
            forms.TableCell(self.btn_send, False),
            forms.TableCell(self.btn_close, False),
        ))

        btn_panel = forms.Panel()
        btn_panel.BackgroundColor = COL_BG
        btn_panel.Height = 32
        btn_panel.Content = btn_row
        layout.AddRow(btn_panel)

        self.Content = layout

    def on_select_curve(self, sender, e):
        self.Visible = False
        try:
            ids = rs.GetObjects(
                message="Select a polyline curve to send",
                filter=rs.filter.curve,
                preselect=True,
            )
            if ids:
                self.selected_curve_ids = list(ids)
                self.lbl_curve_status.Text = "{0} curve(s) selected.".format(len(ids))

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
                self.lbl_curve_status.Text = "No curve selected."
                self.preview.live_points_fn = None
                self.preview.set_points(None)
        finally:
            self.Visible = True

    def on_send(self, sender, e):
        email     = self.txt_email.Text.strip()
        password  = self.txt_password.Text
        name      = self.txt_name.Text.strip()
        is_closed = bool(self.chk_closed.Checked)

        if not email or not password:
            self.lbl_status.Text = "Email and password are required."
            return
        if not name:
            self.lbl_status.Text = "Please enter an A-Line name."
            return
        if not self.captured_pts:
            self.lbl_status.Text = "No curve selected."
            return

        pts, err = extract_2d_points(self.captured_pts, is_closed)
        if err:
            self.lbl_status.Text = err
            return

        self.lbl_status.Text = "Sending..."
        self.btn_send.Enabled = False

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
                self.lbl_status.Text = msg
                self.btn_send.Enabled = True

            Rhino.RhinoApp.InvokeOnUiThread(System.Action(update_ui))

        t = threading.Thread(target=do_send)
        t.daemon = True
        t.start()

    def on_close(self, sender, e):
        self.preview.timer.Stop()
        self.Close()


def main():
    dialog = ALineSenderDialog()
    main_win = Rhino.UI.RhinoEtoApp.MainWindow
    dialog.Owner = main_win
    dialog.Location = drawing.Point(
        main_win.Location.X + 60,
        main_win.Location.Y + 60,
    )
    dialog.Show()


if __name__ == "__main__":
    main()
