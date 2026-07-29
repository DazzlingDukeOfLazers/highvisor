# SEO keywords and page mapping — Round 2

Factual accuracy outranks keyword work. Apply these only after the P0 contract tickets. Use one primary phrase plus two or three natural supporting phrases in the H1/opening paragraph; never dump a keyword list into public prose.

## highvisor

### README

- Primary: **desktop automation for background windows**
- Supporting: macOS and Windows desktop automation; control an unfocused app; AI desktop control; local automation daemon
- Current H1 is already strong. Optional fuller title:

> highvisor: desktop automation and background window control for macOS and Windows

### `docs/00-overview.md`

- Primary: **background desktop automation**
- Supporting: visual parity testing; AI agent coordination; cross-platform window automation
- Suggested H1:

> highvisor overview: background desktop automation, visual parity, and agent coordination

### `docs/01-architecture.md`

- Primary: **cross-platform desktop automation architecture**
- Supporting: macOS AX automation; Windows UI Automation; targeted window input; local JSON RPC
- Put “architecture” plus platform/category language before internal layer names.

### `docs/05-driving-input.md`

- Primary: **synthetic mouse and keyboard input for desktop apps**
- Supporting: macOS CGEvent; Windows PostMessage; Unity synthetic click; background window input
- Search-intent headings worth keeping: “send keyboard input to a background window,” “Unity/Qud mouse click,” and “window points vs screenshot pixels.”

### `docs/06-agent-loop.md`

- Primary: **human-in-the-loop AI agent orchestration**
- Supporting: desktop AI agent coordination; gated agent automation; local multi-agent workflow
- Suggested H1:

> Human-in-the-loop desktop agent coordination with highvisor opcodes

### `docs/07-ssh-transport.md`

- Primary: **remote desktop automation over SSH**
- Supporting: SSH tunnel local RPC; encrypted cross-machine automation; remote window control
- Suggested H1:

> Remote desktop automation over SSH with highvisor

### `docs/08-parity-kit.md`

- Primary: **visual regression testing with screenshot diffs**
- Supporting: golden image testing; UI parity testing; cross-application screenshot comparison
- Current lede already matches reader intent well.

## raves-of-qud

### README

- Primary: **Caves of Qud 3D viewer**
- Supporting: Caves of Qud mod; Godot front end for Caves of Qud; 2.5D roguelike viewer
- Current H1 is good. Prefer the natural-order variant if touched:

> Raves of Qud: a 2.5D/3D Godot viewer for Caves of Qud

### `docs/architecture.md`

- Primary: **Caves of Qud Godot architecture**
- Supporting: C# mod bridge; Qud thread model; Unity main thread; Godot game-state client
- Suggested H1:

> Raves of Qud architecture: Godot client, C# bridge, and Qud thread model

### `docs/protocol.md`

- Primary: **Caves of Qud mod-to-Godot protocol**
- Supporting: Godot TCP JSON bridge; Caves of Qud mod API; Unity game-state bridge
- Suggested H1:

> Caves of Qud mod-to-Godot bridge protocol

### `docs/rendering.md`

- Primary: **Caves of Qud tile rendering in Godot**
- Supporting: Godot voxel walls; 2.5D tile rendering; billboard sprites; roguelike lighting
- Current H1 and mental-model lede are already good.

### `docs/qud-api.md`

- Primary: **Caves of Qud mod API reference**
- Supporting: Qud C# modding; Assembly-CSharp reflection; XRL API signatures
- Suggested H1:

> Verified Caves of Qud C# mod API reference

### `docs/cameras.md`

- Primary: **Raves of Qud camera controls**
- Supporting: Godot multi-camera view; 2.5D camera controls; top-down orthographic camera
- Current H1 is good.

### `docs/legacy-integration-playbook.md`

- Primary: **Godot legacy game integration**
- Supporting: Unity-to-Godot bridge; background game automation; game-state socket bridge

### `docs/multiplayer.md`

- Primary: **Caves of Qud multiplayer proposal**
- Supporting: Caves of Qud co-op architecture; Godot WebRTC game networking
- Keep “proposal” in the H1/opening until it exists.

## Content opportunities bigger than keywords

- Add one representative screenshot/GIF to each root README only when it can be kept current; use descriptive alt text naming the product and visible result.
- Keep one descriptive H1 per page.
- Put tested version/date in a status note, not an evergreen title.
- Use descriptive link text; do not make readers decode “docs/05.”
- Prefer current-status tables over date-stamped claims scattered through prose.
- For niche technical pages, define internal terms (“Holodeck,” “tier,” “bridge”) after the plain-language category, not before it.
