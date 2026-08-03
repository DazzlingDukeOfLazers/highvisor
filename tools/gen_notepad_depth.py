"""Depth pass: turn the static Notepad golem into an INTERACTIVE one.

Builds on gen_notepad.py. It re-emits the same static skeleton (region bands,
labels, toolbar chips — the thing that already scores ~99% pixel-match) and then
overlays an interactive layer driven by a walk of the *real* Notepad:

  * every interactive zone gets a transparent Button on top of its placeholder,
    so it shows the WinUI hover highlight (subtle white overlay) on mouse-over
    and a stronger fill on press — matching what the source does;
  * toggles (Bold/Italic/Strikethrough) keep their pressed/active fill;
  * menu / dropdown / flyout zones open a styled popup populated from the
    CATALOG recorded during the walk (items, accelerators, separators, disabled
    rows, checkmarks, submenu chevrons) — visual only, no real functionality;
  * the three "big" surfaces reproduce their shape: What's new -> centred modal
    card, Settings -> full-window page with a back arrow, Avatar -> account card
    (placeholder identity, NOT the user's real name/email).

Geometry comes from the UIA tree (physical bounds -> logical /2), so the popups
anchor under their true buttons. The catalog is the human-readable payload of the
depth walk; swap it and you reshape the golem without touching the renderer.

Cross-platform: inputs and output are configurable via CLI flags and default to
the fixtures shipped alongside this script (tools/fixtures/), so the same command
regenerates the golem on macOS or Windows:

    python tools/gen_notepad_depth.py            # uses bundled fixtures -> ./notepad_golem
    python tools/gen_notepad_depth.py --out /tmp/golem

Then open it with Godot 4.7:
    macOS:   /Applications/Godot.app/Contents/MacOS/Godot --path notepad_golem
    Windows: Godot_v4.7.1-stable_win64.exe --path notepad_golem
"""
import argparse
import io
import json
import os

from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES = os.path.join(_HERE, "fixtures")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tree", default=os.path.join(_FIXTURES, "notepad_tree.json"),
                   help="UIA tree JSON from `hv inspect` (default: bundled fixture)")
    p.add_argument("--png", default=os.path.join(_FIXTURES, "notepad.png"),
                   help="window capture from `hv shot` (default: bundled fixture)")
    p.add_argument("--out", default=os.path.join(os.getcwd(), "notepad_golem"),
                   help="output Godot project dir (default: ./notepad_golem)")
    p.add_argument("--scale", type=int, default=2,
                   help="physical/logical px ratio, i.e. DPI scale (default: 2 for 200%%)")
    return p.parse_args()


args = parse_args()
TREE = args.tree
PNG = args.png
OUT = args.out
S = args.scale

tree = json.load(io.open(TREE, encoding="utf-8"))["tree"]
img = Image.open(PNG).convert("RGB")
OX, OY, WW, WH = tree["bounds"]
LW, LH = WW // S, WH // S

flat = []
def walk(n):
    flat.append(n)
    for c in n["children"]:
        walk(c)
walk(tree)

def find(role, name=None, contains=None):
    for n in flat:
        if n["role"] != role:
            continue
        if name is not None and n["name"] != name:
            continue
        if contains is not None and contains not in n["name"]:
            continue
        return n
    return None

def sample(px, py):
    x, y = max(0, min(WW - 1, px - OX)), max(0, min(WH - 1, py - OY))
    return img.getpixel((x, y))

def col(rgb, a=1):
    return "Color(%.4f, %.4f, %.4f, %s)" % (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, a)

TEXT = "Color(0.92, 0.92, 0.92, 1)"
c_tab = sample(OX + 300, OY + 50)
c_bar = sample(OX + WW // 2, OY + 115)
c_edit = sample(OX + WW // 2, OY + 700)
c_stat = sample(OX + WW // 2, OY + 1170)
c_btn = tuple(min(255, v + 24) for v in c_bar)

def rect(node):
    bx, by, bw, bh = node["bounds"]
    return ((bx - OX) / S, (by - OY) / S, bw / S, bh / S)

def band(px, py, pw, ph):
    return ((px - OX) / S, (py - OY) / S, pw / S, ph / S)

# ---- static skeleton (identical to gen_notepad.py) -------------------------
parts = []
_uid = [0]

def node(name, typ, l, t, w, h, color=None, text=None, font=15, parent="."):
    _uid[0] += 1
    s = ['[node name="%s%d" type="%s" parent="%s"]' % (name, _uid[0], typ, parent),
         "layout_mode = 0" if parent == "." else "layout_mode = 2",
         "offset_left = %.1f" % l, "offset_top = %.1f" % t,
         "offset_right = %.1f" % (l + w), "offset_bottom = %.1f" % (t + h)]
    if color is not None:
        s.append("color = " + color)
    if text is not None:
        s.append("theme_override_font_sizes/font_size = %d" % font)
        s.append("theme_override_colors/font_color = " + TEXT)
        s.append("text = \"%s\"" % text)
    parts.append("\n".join(s) + "\n")

parts.append('[node name="Main" type="Control"]\n'
             "layout_mode = 3\nanchors_preset = 15\n"
             "anchor_right = 1.0\nanchor_bottom = 1.0\n"
             'script = ExtResource("1_depth")\n')

tab = find("TabItemControl", contains="Untitled")
edit = find("DocumentControl", name="Text editor")
node("Bg", "ColorRect", 0, 0, LW, LH, color=col(c_edit))
node("TabBand", "ColorRect", *band(OX, OY + 18, WW, 64), color=col(c_tab))
node("BarBand", "ColorRect", *band(OX, OY + 82, WW, 66), color=col(c_bar))
node("Editor", "ColorRect", *rect(edit), color=col(c_edit))
node("StatusBand", "ColorRect", *band(OX, OY + 1136, WW, 64), color=col(c_stat))

tt = find("TextControl", name="Untitled")
if tt:
    l, t, w, h = rect(tt)
    node("TabTitle", "Label", l, t, w + 40, h, text="Untitled", font=15)
if tab:
    node("TabChip", "ColorRect", *rect(tab), color=col(c_btn, 0.5))

for mn in ("File", "Edit", "View"):
    m = find("MenuItemControl", name=mn)
    if m:
        l, t, w, h = rect(m)
        node(mn, "Label", l + 8, t + 6, w, h - 12, text=mn, font=15)

GLYPH = {"Headings": "H1", "Lists": "list", "Bold (Ctrl+B)": "B",
         "Italic (Ctrl+I)": "I", "Strikethrough (Ctrl+Shift+X)": "S",
         "Link (Ctrl+K)": "link", "Table": "tbl",
         "Clear formatting (Ctrl+Space)": "clr", "Writing tools": "AI",
         "What's new": "!", "User avatar": "@", "Settings": "gear"}
for full, short in GLYPH.items():
    b = find("ButtonControl", name=full)
    if not b:
        continue
    l, t, w, h = rect(b)
    node("btnchip", "ColorRect", l + 1, t + 4, w - 2, h - 8, color=col(c_btn))
    node("btntxt", "Label", l + 4, t + 6, w, h - 12, text=short, font=13)

STATUS = [("0 characters", "0 characters"), ("Plain text", "Plain text"),
          ("Zoom", "100%"), ("Windows (CRLF)", "Windows (CRLF)"),
          ("UTF-8", "UTF-8")]
for search, render in STATUS:
    tn = find("TextControl", contains=search)
    if tn:
        l, t, w, h = rect(tn)
        node("st", "Label", l, t + 4, w + 40, h, text=render, font=13)
ln = find("TextControl", contains="Line 1")
if ln:
    l, t, w, h = rect(ln)
    node("st", "Label", l, t + 4, w + 40, h, text="Ln 1, Col 1", font=13)

# ---- depth catalog (what the walk of real Notepad recorded) ----------------
# item = [label, accel, flags]  flags in {"", "disabled", "sep", "sub", "check"}
CATALOG = {
    "File": ("list", [
        ["New tab", "Ctrl+N", ""], ["New window", "Ctrl+Shift+N", ""],
        ["New Markdown tab", "", ""], ["Open", "Ctrl+O", ""],
        ["Recent", "", "sub"], ["", "", "sep"],
        ["Save", "Ctrl+S", ""], ["Save as", "Ctrl+Shift+S", ""],
        ["Save all", "Ctrl+Alt+S", ""], ["", "", "sep"],
        ["Page setup", "", ""], ["Print", "Ctrl+P", ""], ["", "", "sep"],
        ["Close tab", "Ctrl+W", ""], ["Close window", "Ctrl+Shift+W", ""],
        ["", "", "sep"], ["Exit", "", ""]]),
    "Edit": ("list", [
        ["Undo", "Ctrl+Z", "disabled"], ["Cut", "Ctrl+X", "disabled"],
        ["Copy", "Ctrl+C", "disabled"], ["Paste", "Ctrl+V", ""],
        ["Delete", "Del", "disabled"], ["", "", "sep"],
        ["Clear formatting", "", ""], ["Search with Bing", "Ctrl+E", "disabled"],
        ["", "", "sep"], ["Find", "Ctrl+F", "disabled"],
        ["Find next", "F3", "disabled"], ["Find previous", "Shift+F3", "disabled"],
        ["Replace", "Ctrl+H", "disabled"], ["Go to", "Ctrl+G", ""],
        ["", "", "sep"], ["Select all", "Ctrl+A", ""], ["Time/Date", "F5", ""],
        ["", "", "sep"], ["Font", "", ""]]),
    "View": ("list", [
        ["Zoom", "", "sub"], ["Status bar", "", "check"],
        ["Word wrap", "", "check"], ["Markdown", "", "sub disabled"]]),
    "Lists": ("list", [
        ["Bulleted list", "", ""], ["Numbered list", "", ""],
        ["Increase indent", "", "disabled"], ["Decrease indent", "", "disabled"]]),
    "Writing tools": ("list", [
        ["Write", "Ctrl+Q", "new"], ["Rewrite", "Ctrl+D", "disabled"],
        ["Summarize", "Ctrl+M", "disabled"], ["Make shorter", "", "disabled"],
        ["Make longer", "", "disabled"], ["Change tone", "", "sub disabled"],
        ["Change format", "", "sub disabled"]]),
    "Headings": ("headings", [
        ["Title", 26, ""], ["Subtitle", 20, ""], ["Heading", 18, ""],
        ["Subheading", 16, ""], ["Section", 15, ""], ["Subsection", 14, ""],
        ["Body", 14, "sel"]]),
    "Table": ("table", []),
    "Link (Ctrl+K)": ("link", []),
    "What's new": ("modal", []),
    "User avatar": ("account", []),
    "Settings": ("page", []),
}
KIND_BY_FULL = {
    "Bold (Ctrl+B)": "toggle", "Italic (Ctrl+I)": "toggle",
    "Strikethrough (Ctrl+Shift+X)": "toggle",
    "Clear formatting (Ctrl+Space)": "action",
}

# Build the zones payload the GDScript consumes: id, rect, kind, items.
zones = []
def add_zone(zid, node_role, node_name, kind, items):
    n = find(node_role, name=node_name)
    if not n:
        return
    l, t, w, h = rect(n)
    zones.append({"id": zid, "rect": [round(l, 1), round(t, 1),
                                      round(w, 1), round(h, 1)],
                  "kind": kind, "items": items})

for mn in ("File", "Edit", "View"):
    kind, items = CATALOG[mn]
    add_zone(mn, "MenuItemControl", mn, kind, items)

for full in GLYPH:
    if full in KIND_BY_FULL:
        add_zone(full, "ButtonControl", full, KIND_BY_FULL[full], [])
    elif full in CATALOG:
        kind, items = CATALOG[full]
        add_zone(full, "ButtonControl", full, kind, items)

DATA_JSON = json.dumps(zones, separators=(",", ":"))

# ---- write files -----------------------------------------------------------
os.makedirs(OUT, exist_ok=True)

tscn = "[gd_scene load_steps=2 format=3]\n\n" \
       '[ext_resource type="Script" path="res://main.gd" id="1_depth"]\n\n' \
       + "\n".join(parts)
io.open(os.path.join(OUT, "main.tscn"), "w", encoding="utf-8").write(tscn)

# main.gd — the interactive renderer. DATA is injected as a JSON string.
GD = r'''extends Control
# Auto-generated interactive layer for the Notepad golem (depth pass).
# Static skeleton lives in main.tscn; this script overlays the behaviour.

const DATA := "__DATA__"

const FG := Color(0.92, 0.92, 0.92)
const DIM := Color(0.62, 0.62, 0.62)
const PANEL_BG := Color(0.16, 0.16, 0.16)
const PANEL_BORDER := Color(0.32, 0.32, 0.32)
const ACCENT := Color(0.53, 0.62, 0.94)

var _overlay: Control = null
var _open_id := ""

func _ready() -> void:
	var zones = JSON.parse_string(DATA)
	for z in zones:
		_make_zone(z)

func _sb(c: Color, radius := 6, border := 0, bcol := Color(0,0,0,0)) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = c
	sb.set_corner_radius_all(radius)
	if border > 0:
		sb.set_border_width_all(border)
		sb.border_color = bcol
	return sb

func _make_zone(z) -> void:
	var r = z["rect"]
	var kind = z["kind"]
	var btn := Button.new()
	btn.flat = true
	btn.focus_mode = Control.FOCUS_NONE
	btn.position = Vector2(r[0], r[1])
	btn.size = Vector2(r[2], r[3])
	btn.add_theme_stylebox_override("normal", _sb(Color(0,0,0,0)))
	btn.add_theme_stylebox_override("hover", _sb(Color(1,1,1,0.06)))
	btn.add_theme_stylebox_override("pressed", _sb(Color(1,1,1,0.12)))
	btn.add_theme_stylebox_override("focus", _sb(Color(0,0,0,0)))
	add_child(btn)
	if kind == "toggle":
		btn.toggle_mode = true
		btn.add_theme_stylebox_override("hover_pressed", _sb(Color(1,1,1,0.14)))
	elif kind == "action":
		pass
	else:
		btn.pressed.connect(_on_zone.bind(z))

func _on_zone(z) -> void:
	var zid = str(z["id"])
	var was_open = _open_id
	_close()
	if was_open == zid:
		return
	_open_id = zid
	var ov := Control.new()
	ov.set_anchors_preset(Control.PRESET_FULL_RECT)
	ov.mouse_filter = Control.MOUSE_FILTER_PASS
	add_child(ov)
	_overlay = ov
	var r = z["rect"]
	var kind = z["kind"]
	# The Settings page fills the whole window. A flat Button draws no background,
	# so lay an opaque full-rect ColorRect behind everything (mouse-transparent so
	# the dismiss backdrop above it still receives clicks).
	if kind == "page":
		var fill := ColorRect.new()
		fill.color = Color(0.12, 0.12, 0.12)
		fill.set_anchors_preset(Control.PRESET_FULL_RECT)
		fill.mouse_filter = Control.MOUSE_FILTER_IGNORE
		ov.add_child(fill)
	var back := Button.new()
	back.flat = true
	back.focus_mode = Control.FOCUS_NONE
	back.set_anchors_preset(Control.PRESET_FULL_RECT)
	for st in ["normal", "hover", "pressed", "focus"]:
		back.add_theme_stylebox_override(st, _sb(Color(0,0,0,0), 0))
	back.pressed.connect(_close)
	ov.add_child(back)
	var panel: Control
	match kind:
		"modal": panel = _modal()
		"page": panel = _page()
		"account": panel = _account()
		"table": panel = _table()
		"headings": panel = _headings(z["items"])
		"link": panel = _link()
		_: panel = _list(z["items"])
	ov.add_child(panel)
	if kind == "page":
		panel.set_anchors_preset(Control.PRESET_FULL_RECT)
	elif kind == "modal":
		await get_tree().process_frame
		panel.position = (size - panel.size) * 0.5
	else:
		panel.position = Vector2(r[0], r[1] + r[3] + 2.0)
		await get_tree().process_frame
		if panel.position.x + panel.size.x > size.x - 4.0:
			panel.position.x = max(4.0, size.x - panel.size.x - 4.0)

func _close() -> void:
	if _overlay and is_instance_valid(_overlay):
		_overlay.queue_free()
	_overlay = null
	_open_id = ""

# ---- panel builders --------------------------------------------------------
func _panel() -> PanelContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", _sb(PANEL_BG, 8, 1, PANEL_BORDER))
	return p

func _pad(c: Control, m := 6) -> MarginContainer:
	var mc := MarginContainer.new()
	for s in ["left", "right", "top", "bottom"]:
		mc.add_theme_constant_override("margin_" + s, m)
	mc.add_child(c)
	return mc

func _lbl(t: String, sz := 14, c := FG) -> Label:
	var l := Label.new()
	l.text = t
	l.add_theme_font_size_override("font_size", sz)
	l.add_theme_color_override("font_color", c)
	return l

func _sep() -> HSeparator:
	var s := HSeparator.new()
	s.add_theme_stylebox_override("separator", _sb(PANEL_BORDER, 0))
	s.add_theme_constant_override("separation", 5)
	return s

func _row(item) -> Control:
	var label = item[0]
	var accel = item[1]
	var flags = str(item[2])
	if flags.find("sep") != -1:
		return _sep()
	var h := HBoxContainer.new()
	h.custom_minimum_size = Vector2(228, 30)
	h.add_theme_constant_override("separation", 12)
	var left := _lbl(("✓  " if flags.find("check") != -1 else "") + label)
	h.add_child(left)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(spacer)
	if flags.find("new") != -1:
		var badge := _lbl("New", 11, Color(0.85, 0.7, 0.95))
		h.add_child(badge)
	if flags.find("sub") != -1:
		h.add_child(_lbl("›", 15, DIM))
	elif accel != "":
		h.add_child(_lbl(accel, 13, DIM))
	var wrap := MarginContainer.new()
	for s in ["left", "right"]:
		wrap.add_theme_constant_override("margin_" + s, 8)
	wrap.add_theme_constant_override("margin_top", 3)
	wrap.add_theme_constant_override("margin_bottom", 3)
	wrap.add_child(h)
	if flags.find("disabled") != -1:
		wrap.modulate = Color(1, 1, 1, 0.4)
	return wrap

func _list(items) -> PanelContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 1)
	for it in items:
		v.add_child(_row(it))
	var p := _panel()
	p.add_child(_pad(v, 5))
	return p

func _headings(items) -> PanelContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 4)
	for it in items:
		var row := PanelContainer.new()
		var sel = str(it[2]) == "sel"
		row.add_theme_stylebox_override("panel",
			_sb(Color(1,1,1,0.06) if sel else Color(0,0,0,0), 4))
		var hb := HBoxContainer.new()
		if sel:
			var bar := ColorRect.new()
			bar.color = ACCENT
			bar.custom_minimum_size = Vector2(3, 0)
			hb.add_child(bar)
		var m := MarginContainer.new()
		m.add_theme_constant_override("margin_left", 10)
		m.add_theme_constant_override("margin_right", 20)
		m.add_theme_constant_override("margin_top", 2)
		m.add_theme_constant_override("margin_bottom", 2)
		m.add_child(_lbl(it[0], int(it[1])))
		hb.add_child(m)
		row.add_child(hb)
		v.add_child(row)
	var p := _panel()
	p.add_child(_pad(v, 6))
	return p

func _table() -> PanelContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	v.add_child(_lbl("Insert table", 13, DIM))
	var grid := GridContainer.new()
	grid.columns = 5
	grid.add_theme_constant_override("h_separation", 4)
	grid.add_theme_constant_override("v_separation", 4)
	for i in range(25):
		var cell := PanelContainer.new()
		cell.custom_minimum_size = Vector2(20, 20)
		cell.add_theme_stylebox_override("panel", _sb(Color(0,0,0,0), 2, 1, PANEL_BORDER))
		grid.add_child(cell)
	v.add_child(grid)
	v.add_child(_lbl("Insert table", 14))
	var edit := _lbl("Edit table  ›", 14, DIM)
	edit.modulate = Color(1, 1, 1, 0.5)
	v.add_child(edit)
	var p := _panel()
	p.add_child(_pad(v, 10))
	return p

func _link() -> PanelContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 6)
	v.add_child(_lbl("Insert link", 15))
	for cap in ["Text to display", "Address"]:
		v.add_child(_lbl(cap, 12, DIM))
		var box := PanelContainer.new()
		box.custom_minimum_size = Vector2(240, 26)
		box.add_theme_stylebox_override("panel", _sb(Color(0.1,0.1,0.1), 4, 1, PANEL_BORDER))
		v.add_child(box)
	var ins := Button.new()
	ins.text = "Insert"
	ins.add_theme_stylebox_override("normal", _sb(ACCENT, 4))
	v.add_child(ins)
	var p := _panel()
	p.add_child(_pad(v, 12))
	return p

func _account() -> PanelContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 4)
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 10)
	var av := PanelContainer.new()
	av.custom_minimum_size = Vector2(34, 34)
	av.add_theme_stylebox_override("panel", _sb(Color(0.35,0.4,0.5), 17))
	top.add_child(av)
	var who := VBoxContainer.new()
	who.add_child(_lbl("Account name", 14))
	who.add_child(_lbl("name@example.com", 12, DIM))
	top.add_child(who)
	v.add_child(_pad(top, 6))
	v.add_child(_sep())
	var so := HBoxContainer.new()
	so.add_theme_constant_override("separation", 10)
	so.add_child(_lbl("↪", 15, FG))
	so.add_child(_lbl("Sign out", 14))
	v.add_child(_pad(so, 6))
	var p := _panel()
	p.add_child(_pad(v, 4))
	return p

func _modal() -> PanelContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", _sb(Color(0.13,0.13,0.13), 10, 1, PANEL_BORDER))
	p.custom_minimum_size = Vector2(430, 300)
	var root := VBoxContainer.new()
	root.add_theme_constant_override("separation", 8)
	var head := HBoxContainer.new()
	head.add_child(_lbl("New in Notepad", 18))
	var sp := Control.new()
	sp.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(sp)
	var x := Button.new()
	x.text = "✕"
	x.flat = true
	x.pressed.connect(_close)
	head.add_child(x)
	root.add_child(head)
	var body := HBoxContainer.new()
	body.add_theme_constant_override("separation", 16)
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var feats := VBoxContainer.new()
	feats.add_theme_constant_override("separation", 10)
	feats.custom_minimum_size = Vector2(160, 0)
	for f in [["Your essential text editor, elevated", false],
			  ["Smarter writing tools", true], ["Lightweight formatting", true]]:
		var fr := HBoxContainer.new()
		fr.add_child(_lbl(f[0], 13))
		if f[1]:
			fr.add_child(_lbl("  New", 10, Color(0.85,0.7,0.95)))
		feats.add_child(fr)
	body.add_child(feats)
	var preview := ColorRect.new()
	preview.color = Color(0.22, 0.2, 0.32)
	preview.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	preview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(preview)
	root.add_child(body)
	root.add_child(_lbl("Your essential text editor, elevated", 15))
	root.add_child(_lbl("The Notepad you know, with flexible formatting and a distraction-free space.", 12, DIM))
	var go := HBoxContainer.new()
	var gsp := Control.new()
	gsp.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	go.add_child(gsp)
	var se := Button.new()
	se.text = "Start exploring"
	se.add_theme_stylebox_override("normal", _sb(Color(0.72,0.5,0.9), 5))
	go.add_child(se)
	root.add_child(go)
	p.add_child(_pad(root, 16))
	return p

func _page() -> PanelContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", _sb(Color(0.12,0.12,0.12), 0))
	var root := VBoxContainer.new()
	root.add_theme_constant_override("separation", 12)
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 10)
	var back := Button.new()
	back.text = "‹"
	back.flat = true
	back.add_theme_font_size_override("font_size", 20)
	back.pressed.connect(_close)
	top.add_child(back)
	root.add_child(top)
	root.add_child(_lbl("Settings", 30))
	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 40)
	var left := VBoxContainer.new()
	left.add_theme_constant_override("separation", 8)
	left.custom_minimum_size = Vector2(340, 0)
	left.add_child(_lbl("Appearance", 12, DIM))
	left.add_child(_setting_card("App theme", "Select which app theme to display", true, false))
	left.add_child(_lbl("Text Formatting", 12, DIM))
	left.add_child(_setting_card("Font", "", true, false))
	left.add_child(_setting_card("Word wrap", "Fit text within window by default", false, true))
	left.add_child(_setting_card("Formatting", "", false, true))
	left.add_child(_lbl("Opening Notepad", 12, DIM))
	left.add_child(_setting_card("Opening files", "Choose where your files are opened", true, false))
	cols.add_child(left)
	var right := VBoxContainer.new()
	right.add_theme_constant_override("separation", 6)
	right.add_child(_lbl("About this app", 14))
	right.add_child(_lbl("Windows Notepad", 12, DIM))
	right.add_child(_lbl("© Microsoft. All rights reserved.", 12, DIM))
	for link in ["Microsoft Software License Terms", "Microsoft Services Agreement",
				 "Microsoft Privacy Statement", "Third-Party Software Acknowledgments"]:
		right.add_child(_lbl(link, 12, ACCENT))
	cols.add_child(right)
	root.add_child(cols)
	p.add_child(_pad(root, 24))
	return p

func _setting_card(title: String, sub: String, chev: bool, toggle: bool) -> PanelContainer:
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", _sb(Color(0.17,0.17,0.17), 6, 1, PANEL_BORDER))
	card.custom_minimum_size = Vector2(320, 0)
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 10)
	var tv := VBoxContainer.new()
	tv.add_child(_lbl(title, 14))
	if sub != "":
		tv.add_child(_lbl(sub, 11, DIM))
	h.add_child(tv)
	var sp := Control.new()
	sp.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(sp)
	if toggle:
		var pill := PanelContainer.new()
		pill.custom_minimum_size = Vector2(36, 18)
		pill.add_theme_stylebox_override("panel", _sb(ACCENT, 9))
		h.add_child(pill)
	elif chev:
		h.add_child(_lbl("⌄", 16, DIM))
	card.add_child(_pad(h, 10))
	return card
'''

GD = GD.replace("__DATA__", DATA_JSON.replace("\\", "\\\\").replace('"', '\\"'))
io.open(os.path.join(OUT, "main.gd"), "w", encoding="utf-8").write(GD)

io.open(os.path.join(OUT, "project.godot"), "w", encoding="utf-8").write(
    'config_version=5\n\n[application]\n\n'
    'config/name="HV Notepad Ersatz"\n'
    'run/main_scene="res://main.tscn"\n'
    'config/features=PackedStringArray("4.7")\n\n'
    '[display]\n\n'
    'window/size/viewport_width=%d\n'
    'window/size/viewport_height=%d\n'
    'window/size/resizable=true\n'
    'window/size/borderless=true\n'
    'window/stretch/mode="canvas_items"\n'
    'window/stretch/aspect="keep"\n' % (LW, LH))

print("wrote interactive golem to %s (base %dx%d, %d zones)"
      % (OUT, LW, LH, len(zones)))
for z in zones:
    print("  zone %-28s %-8s rect=%s" % (z["id"], z["kind"], z["rect"]))
