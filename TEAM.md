# LANBoard team

Computer Networks Lab Project 2026, IIITDM Kancheepuram.
Project: LAN-based multi-user collaborative whiteboard.

- GitHub repository: <https://github.com/YashvanthSankar/cn-course-project>
- Downloads (Windows, macOS, Linux): <https://github.com/YashvanthSankar/cn-course-project/releases>

| # | Name | Roll no. | Area presented |
|---|---|---|---|
| 1 | Yashvanth S | CS24I1029 | Server core and state |
| 2 | Jeevanandham T | CS24I1035 | Protocol and networking |
| 3 | Akula Sai Teja | CS24I1054 | Canvas and drawing tools |
| 4 | Nithillakrishi P Y | CS24I1039 | UI and collaboration features |

## Explanation video

`video/LANBoard_Explanation_Video.mp4` (23 min 33 s) holds all four explanations in order, followed
by a short live demo. Each member's name and face are visible while they present, and the file
has a chapter for each part. The same parts are also given as separate files in
`video/parts/`.

| Starts at | Part | Presented by | Separate file |
|---|---|---|---|
| 0:00 | Server core and state | Yashvanth S (CS24I1029) | `1_Yashvanth_S_CS24I1029_server_core.mp4` |
| 4:20 | Protocol and networking | Jeevanandham T (CS24I1035) | `2_Jeevanandham_T_CS24I1035_networking.mp4` |
| 10:34 | Canvas and drawing tools | Akula Sai Teja (CS24I1054) | `3_Akula_Sai_Teja_CS24I1054_canvas.mp4` |
| 14:51 | UI and collaboration features | Nithillakrishi P Y (CS24I1039) | `4_Nithillakrishi_P_Y_CS24I1039_ui.mp4` |
| 20:18 | Live demo (optional extra) | Akula Sai Teja (CS24I1054) | `5_Live_demo.mp4` |

## Files behind each area

Paths are relative to `src/lanboard/`.

| Area | Main files |
|---|---|
| Server core and state | `server/server.py`, `server/room.py`, `server/persistence.py`, `server/dashboard.py`, `common/model.py` |
| Protocol and networking | `common/protocol.py`, `common/messages.py`, `client/net.py`, `client/discovery.py`, `server/discovery.py`, `client/controller.py` (gaps, duplicates, resync), `client/chaos.py`, `client/metrics.py` |
| Canvas and drawing tools | `client/canvas/`, `common/geometry.py`, `common/undo.py` |
| UI and collaboration features | `client/ui/`, `packaging/` (Windows, macOS and Linux builds) |
| Shared | `common/auth.py`, `common/config.py` |
