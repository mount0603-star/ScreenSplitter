import sys
import os
import json
import subprocess
from PySide6.QtCore import Qt, QTimer, QSize, QRectF, Signal, QRect, QPoint, QUrl
from PySide6.QtGui import QIcon, QFont, QAction, QPainter, QColor, QPen, QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QLineEdit, QComboBox, 
                               QPushButton, QFormLayout, QGroupBox, QListWidget,
                               QSpinBox, QScrollArea, QSplitter, QCheckBox, QGridLayout, QFrame, QStyledItemDelegate, QStyle, QAbstractItemView, QListWidgetItem, QMessageBox)

import config_manager

class ResizableZone(QWidget):
    clicked = Signal(int)
    geometry_changed = Signal(int, QRect)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.is_active = False
        self.setMouseTracking(True)
        
        # State
        self.mode = None # None, 'drag', 'resize'
        self.start_global_pos = None
        self.start_geometry = None
        self.resize_margin = 15 
        
        # Snap/Collision Settings
        self.snap_threshold = 10 
        
        # Style
        self.setAutoFillBackground(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        
        # Determine Color based on Collision/State
        bg_alpha = 50 if self.is_active else 200
        bg_color = QColor(0, 210, 255, bg_alpha)
        
        # Check collision with siblings (simple highlight)
        if self.check_collision(self.geometry()):
            bg_color = QColor(255, 50, 50, 150) # Red if colliding
            
        if not self.is_active and not self.mode:
            bg_color = QColor(30, 30, 47, 200)

        painter.fillRect(self.rect(), bg_color)
        
        # Border
        border_color = QColor(0, 210, 255) if self.is_active else QColor(100, 100, 100)
        pen = QPen(border_color)
        pen.setWidth(3 if self.is_active else 1)
        pen.setStyle(Qt.SolidLine)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(0,0,-1,-1))
        
        # Text
        painter.setPen(Qt.white)
        font = painter.font()
        font.setPointSize(14)
        font.setBold(self.is_active)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, str(self.index))
        
        # Resize Handle (Bottom Right)
        painter.setBrush(QColor(0, 210, 255))
        painter.setPen(Qt.NoPen)
        # Draw a triangle or square
        h_size = self.resize_margin
        # h_rect = QRect(self.width() - h_size, self.height() - h_size, h_size, h_size)
        # painter.drawRect(h_rect)
        
        # Draw triangle handle
        p1 = transform_point(self.rect().bottomRight())
        painter.drawPolygon([
            self.rect().bottomRight(),
            self.rect().bottomRight() - QPoint(h_size, 0),
            self.rect().bottomRight() - QPoint(0, h_size)
        ])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
            
            # Check resize
            if self.is_in_resize_handle(event.pos()):
                self.mode = 'resize'
            else:
                self.mode = 'drag'
                
            self.start_global_pos = event.globalPosition().toPoint()
            self.start_geometry = self.geometry()
            self.raise_() # Bring to front
            self.update()

    def mouseMoveEvent(self, event):
        # Update Cursor
        if self.is_in_resize_handle(event.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.SizeAllCursor) # Or Arrow if not dragging

        if not self.mode:
            return

        current_global_pos = event.globalPosition().toPoint()
        delta = current_global_pos - self.start_global_pos
        
        new_geo = QRect(self.start_geometry)

        if self.mode == 'drag':
            new_geo.translate(delta)
            # Boundary Clean up (Clamp to parent)
            if self.parentWidget():
                p_rect = self.parentWidget().rect()
                # Clamp X
                if new_geo.left() < 0: new_geo.moveLeft(0)
                if new_geo.right() > p_rect.width(): new_geo.moveRight(p_rect.width())
                # Clamp Y
                if new_geo.top() < 0: new_geo.moveTop(0)
                if new_geo.bottom() > p_rect.height(): new_geo.moveBottom(p_rect.height())
                
            # Snap to Grid/Others (Opt)
            new_geo = self.apply_snap(new_geo)
            
        elif self.mode == 'resize':
            new_w = max(40, self.start_geometry.width() + delta.x())
            new_h = max(40, self.start_geometry.height() + delta.y())
            new_geo.setWidth(new_w)
            new_geo.setHeight(new_h)
            
        self.setGeometry(new_geo)
        self.geometry_changed.emit(self.index, self.geometry())
        self.update()

    def mouseReleaseEvent(self, event):
        self.mode = None
        self.update()
        
    def is_in_resize_handle(self, pos):
        # Bottom right corner
        return (pos.x() >= self.width() - self.resize_margin and 
                pos.y() >= self.height() - self.resize_margin)

    def check_collision(self, rect):
        if not self.parentWidget(): return False
        
        for child in self.parentWidget().children():
            if isinstance(child, ResizableZone) and child != self:
                if child.geometry().intersects(rect):
                    return True
        return False

    def apply_snap(self, rect):
        # Simple snap to edges of other zones
        if not self.parentWidget(): return rect
        
        snap_dist = 10
        
        # Candidate lines
        x_lines = [0, self.parentWidget().width()]
        y_lines = [0, self.parentWidget().height()]
        
        for child in self.parentWidget().children():
            if isinstance(child, ResizableZone) and child != self:
                x_lines.append(child.geometry().left())
                x_lines.append(child.geometry().right() + 1)
                y_lines.append(child.geometry().top())
                y_lines.append(child.geometry().bottom() + 1)
        
        # Snap X (Left or Right)
        # Try snap Left
        for x in x_lines:
            if abs(rect.left() - x) < snap_dist:
                rect.moveLeft(x)
                break
            if abs(rect.right() - x) < snap_dist:
                rect.moveRight(x)
                break
                
        # Snap Y
        for y in y_lines:
            if abs(rect.top() - y) < snap_dist:
                rect.moveTop(y)
                break
            if abs(rect.bottom() - y) < snap_dist:
                rect.moveBottom(y)
                break
                
        return rect

def transform_point(pt):
    # Dummy helper if needed, but not used in this snippet directly
    return pt

# Delegate for Circular Progress
class CircularProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        
        # 1. Draw Background (Selection)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#16213e")) # Keep bg subtle
        
        # 2. Get Data
        text = index.data(Qt.DisplayRole)
        # Store metadata in UserRole: {remaining, total, is_active, is_paused}
        data = index.data(Qt.UserRole)
        
        rect = option.rect
        
        # 3. Draw Text (Left)
        painter.setPen(QColor("#e0e0e0"))
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        
        text_rect = QRectF(rect.x() + 10, rect.y(), rect.width() - 60, rect.height())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        
        # 4. Draw Circle (Right)
        if data and data.get("is_active"):
            rem = data.get("remaining", 0)
            total = data.get("total", 10)
            is_paused = data.get("is_paused", False)
            
            size = 24
            circle_rect = QRectF(rect.right() - size - 10, rect.center().y() - size/2, size, size)
            
            # Track
            pen = QPen(QColor("#333"), 3)
            painter.setPen(pen)
            painter.drawEllipse(circle_rect)
            
            # Progress Arc
            if total > 0:
                prog = rem / total
                angle = int(prog * 360 * 16)
                
                pen.setColor(QColor("#00d2ff")) # Cyan
                if is_paused: pen.setColor(QColor("#ffb74d")) # Orange if paused
                
                painter.setPen(pen)
                painter.drawArc(circle_rect, 90 * 16, angle) # Start from top
            
            # Center Number
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#fff"))
            painter.drawText(circle_rect, Qt.AlignCenter, str(int(rem)))
            
        painter.restore()
        
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 40)

# Tech UI Stylesheet
TECH_STYLE = """
QMainWindow {
    background-color: #1a1a2e; /* Deep dark blue/black */
    color: #e0e0e0;
}
QWidget {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', sans-serif;
}
QGroupBox {
    border: 1px solid #0f3460;
    border-radius: 5px;
    margin-top: 10px;
    background-color: #16213e; /* Slightly lighter box */
    font-weight: bold;
    color: #00d2ff; /* Cyan title */
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
}
QPushButton {
    background-color: #0f3460;
    border: 1px solid #16213e;
    color: #fff;
    padding: 6px 12px;
    border-radius: 4px;
}
QPushButton:hover {
    background-color: #e94560; /* Neon Red/Pink hover */
    border: 1px solid #e94560;
}
QPushButton:pressed {
    background-color: #c0354c;
}
QLineEdit, QSpinBox, QComboBox {
    background-color: #0f3460;
    border: 1px solid #16213e;
    color: white;
    padding: 4px;
    border-radius: 3px;
}
QListWidget {
    background-color: #16213e;
    border: 1px solid #0f3460;
    outline: none;
}
QListWidget::item {
    padding: 8px;
    border-bottom: 1px solid #0f3460;
}
QListWidget::item:selected {
    background-color: #e94560;
    color: white;
}
QScrollArea {
    border: none;
    background-color: #1a1a2e;
}
QScrollBar:vertical {
    border: none;
    background: #0f3460;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #e94560;
    min-height: 20px;
    border-radius: 5px;
}
/* Media Buttons */
/* Media Buttons */
QPushButton#btn_media {
    background-color: transparent;
    border: 3px solid #00d2ff;
    border-radius: 20px; /* Circle */
    font-size: 16px;
    width: 40px;
    height: 40px;
    color: #00d2ff;
}
QPushButton#btn_media:hover {
    background-color: rgba(0, 210, 255, 0.2);
    color: #fff;
    border-color: #fff;
}
/* Toggle Button Override */
QPushButton#btn_media_toggle {
    border-color: #e94560; /* keeping red/pink for toggle default? Or Blue? user said blue circles. */
    color: #e94560;
}
QPushButton#btn_media_toggle:hover {
    background-color: rgba(233, 69, 96, 0.2);
    color: #fff;
}
"""

class AdminWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("螢幕切割輪播控制台 (Screen Splitter Admin) V4")
        self.resize(1100, 750)
        
        # Set Window Icon (Try admin.ico first, then app.ico)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_candidates = ["admin.ico", "app.ico"]
        for icon_name in icon_candidates:
            icon_path = os.path.join(base_dir, icon_name)
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                break
        
        # Apply Tech Style
        self.setStyleSheet(TECH_STYLE)

        self.help_action = QAction("使用說明", self)
        self.help_action.setShortcut("F1")
        self.help_action.triggered.connect(self.open_help)
        self.menuBar().addAction(self.help_action)
        
        self.config = {}
        # Update IPC paths to use centralized config
        from config_manager import DATA_DIR
        self.ipc_cmd_path = os.path.join(DATA_DIR, "ipc_cmd.txt")
        self.ipc_status_path = os.path.join(DATA_DIR, "ipc_status.json")
        
        # Main Layout
        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.main_layout = QHBoxLayout(self.central)

        # Splitter
        self.splitter = QSplitter(Qt.Horizontal)
        self.main_layout.addWidget(self.splitter)
        
        # --- LEFT SIDE: Playlist ---
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)
        
        self.lbl_playlist = QLabel("播放清單 (Playlist)")
        self.lbl_playlist.setStyleSheet("font-size: 18px; color: #00d2ff; font-weight: bold;")
        self.left_layout.addWidget(self.lbl_playlist)
        
        self.scene_list = QListWidget()
        self.scene_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.scene_list.model().rowsMoved.connect(self.sync_playlist_order)
        self.scene_list.currentItemChanged.connect(self.on_scene_selected)
        # Double click to play (Jump)
        self.scene_list.itemDoubleClicked.connect(self.on_scene_double_clicked)
        
        self.btn_add = QPushButton(" ＋ 新增場景")
        self.btn_add.clicked.connect(self.add_scene)
        self.btn_del = QPushButton(" － 刪除場景")
        self.btn_del.clicked.connect(self.remove_scene)
        self.btn_save = QPushButton(" 💾 儲存設定")
        # Override save button color
        self.btn_save.setStyleSheet("""
            QPushButton { 
                background-color: #00d2ff; color: #1a1a2e; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background-color: #00b8e6; }
        """)
        self.btn_save.clicked.connect(self.save_config)

        self.left_layout.addWidget(self.scene_list)
        
        # Action Buttons Layout
        action_layout = QHBoxLayout()
        action_layout.addWidget(self.btn_add)
        action_layout.addWidget(self.btn_del)
        self.left_layout.addLayout(action_layout)
        
        self.left_layout.addWidget(self.btn_save)
        
        self.splitter.addWidget(self.left_panel)
        
        # --- RIGHT SIDE: Scene Details + Player Control ---
        self.right_container = QWidget()
        self.right_container_layout = QVBoxLayout(self.right_container)
        self.right_container_layout.setContentsMargins(0,0,0,0)

        # 1. Top Section (Settings + Controls)
        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        # --- Scene Settings (Left) ---
        self.settings_group = QGroupBox("場景設定 (Scene Settings)")
        self.settings_form = QFormLayout()
        
        self.edit_name = QLineEdit()
        self.edit_name.textChanged.connect(self.update_scene_name_display)
        
        self.spin_duration = QSpinBox()
        self.spin_duration.setSuffix(" 秒")
        self.spin_duration.setRange(1, 9999)
        
        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(1, 10)
        self.spin_rows.valueChanged.connect(self.refresh_grid_ui)
        
        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(1, 10)
        self.spin_cols.valueChanged.connect(self.refresh_grid_ui)
        
        self.btn_apply_grid = QPushButton("更新格子配置 (手動)")
        self.btn_apply_grid.clicked.connect(self.refresh_grid_ui)

        # Resolution UI
        self.lbl_res = QLabel("畫面: 1920x1080")
        self.btn_detect_res = QPushButton("偵測解析度")
        self.btn_detect_res.clicked.connect(self.detect_resolution)
        
        self.settings_form.addRow("場景名稱:", self.edit_name)
        self.settings_form.addRow("播放時間 (秒):", self.spin_duration)
        self.settings_form.addRow("行數 (Rows):", self.spin_rows)
        self.settings_form.addRow("列數 (Cols):", self.spin_cols)
        self.settings_form.addRow("畫布尺寸:", self.lbl_res)
        self.settings_form.addRow("", self.btn_detect_res)
        self.settings_form.addRow(self.btn_apply_grid)
        self.settings_group.setLayout(self.settings_form)
        
        # --- Media Controls (Right) ---
        self.controls_group = QGroupBox("播放控制 (Player Control)")
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setAlignment(Qt.AlignCenter)
        
        # Button Helper
        def make_media_btn(text, cmd=None, size=40):
            btn = QPushButton(text)
            btn.setObjectName("btn_media")
            btn.setFixedSize(size, size)
            btn.setCursor(Qt.PointingHandCursor)
            if cmd:
                btn.clicked.connect(lambda: self.send_ipc_cmd(cmd))
            return btn

        self.btn_prev = make_media_btn("⏮", "PREV")
        
        # Toggle Button (Play/Pause/Run)
        self.btn_toggle = QPushButton("▶") 
        self.btn_toggle.setObjectName("btn_media_toggle")
        self.btn_toggle.setFixedSize(60, 60)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        # Style allows bigger font
        self.btn_toggle.setStyleSheet("""
            QPushButton#btn_media_toggle {
                background-color: transparent;
                border: 3px solid #00d2ff;
                border-radius: 30px;
                font-size: 28px;
                color: #00d2ff;
            }
            QPushButton#btn_media_toggle:hover {
                background-color: #00d2ff;
                color: #1a1a2e;
            }
        """)
        self.btn_toggle.clicked.connect(self.on_toggle_click)
        self.btn_toggle.setToolTip("啟動 / 播放 / 暫停")

        self.btn_next = make_media_btn("⏭", "NEXT")

        self.controls_layout.addStretch()
        self.controls_layout.addWidget(self.btn_prev)
        self.controls_layout.addSpacing(20)
        self.controls_layout.addWidget(self.btn_toggle)
        self.controls_layout.addSpacing(20)
        self.controls_layout.addWidget(self.btn_next)
        self.controls_layout.addStretch()
        
        self.controls_group.setLayout(self.controls_layout)
        
        # Add to Top Layout
        top_layout.addWidget(self.settings_group, 3) # Stretch 3
        top_layout.addWidget(self.controls_group, 2) # Stretch 2
        
        self.right_container_layout.addWidget(top_container)
        
        # 2. Scroll Area for Preview & Zones (Restored)
        self.right_panel = QScrollArea()
        self.right_widget = QWidget()
        self.right_layout = QVBoxLayout(self.right_widget)
        self.right_panel.setWidget(self.right_widget)
        self.right_panel.setWidgetResizable(True)
        
        self.right_container_layout.addWidget(self.right_panel)
        
        # Grid Preview Group
        self.grid_preview_group = QGroupBox("佈局預覽 (Layout Preview)")
        self.preview_container = QWidget()
        self.preview_layout = QVBoxLayout(self.preview_container)
        
        # Canvas for absolute positioning (16:9 aspect ratio approx)
        self.preview_canvas = QFrame()
        self.preview_canvas.setFixedSize(480, 270) # 1920x1080 / 4
        self.preview_canvas.setStyleSheet("background-color: #000; border: 2px solid #0f3460; border-radius: 5px;")
        
        self.preview_layout.addWidget(self.preview_canvas, 0, Qt.AlignCenter)
        self.grid_preview_group.setLayout(self.preview_layout)
        self.right_layout.addWidget(self.grid_preview_group)
        
        # Zones List
        self.zones_group = QGroupBox("格子內容設定 (Zones Configuration)")
        self.zones_layout = QFormLayout()
        self.zones_group.setLayout(self.zones_layout)
        self.right_layout.addWidget(self.zones_group)
        self.right_layout.addStretch()

        self.splitter.addWidget(self.right_container)
        self.splitter.setSizes([250, 750])

        self.zone_widgets = [] # list of dict {combo, edit, x, y, w, h, strip_check}
        self.preview_btns = []

        self.load_config()
        
        # Custom Delegate
        self.delegate = CircularProgressDelegate()
        self.scene_list.setItemDelegate(self.delegate)
        
        # IPC Timer for Status
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.check_player_status)
        self.status_timer.start(200) # Faster update for smooth circle

    def open_help(self):
        path = os.path.join(config_manager.SHARED_ROOT, "docs", "usage.html")
        if not os.path.isfile(path):
            QMessageBox.warning(self, "使用說明", f"找不到說明文件：\n{path}\n請確認安裝檔案完整。")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, "使用說明", f"無法開啟預設瀏覽器，請手動開啟：\n{path}")

    def closeEvent(self, event):
        # Prevent auto-shutdown of player explicitly requested by user
        # self.send_ipc_cmd("EXIT") 
        event.accept()

    def on_scene_double_clicked(self, item):
        row = self.scene_list.row(item)
        if row >= 0:
            print(f"Jumping to scene {row}")
            self.send_ipc_cmd(f"JUMP {row}")
        # Only run if not active
        try:
            if getattr(sys, 'frozen', False):
                # Running as EXE
                exe_dir = os.path.dirname(sys.executable)
                player_exe = os.path.join(config_manager.SHARED_ROOT, "Player", "Player.exe")
                if not os.path.exists(player_exe):
                    player_exe = os.path.join(exe_dir, "Player.exe")
                subprocess.Popen([player_exe, "--profile", config_manager.get_active_profile()],
                                 cwd=os.path.dirname(player_exe))
            else:
                # Running as Script
                script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player.py")
                subprocess.Popen([sys.executable, script, "--profile", config_manager.get_active_profile()],
                                 cwd=os.path.dirname(script))
        except Exception as e:
            print(f"Failed to run player: {e}")

    def on_toggle_click(self):
        import time
        now = time.time()
        
        is_running = False
        is_paused = False
        
        if os.path.exists(self.ipc_status_path):
            try:
                with open(self.ipc_status_path, "r", encoding="utf-8") as f:
                    status = json.load(f)
                    ts = status.get("timestamp", 0)
                    if now - ts < 3.0: 
                        is_running = True
                        is_paused = status.get("paused", False)
            except:
                pass
        
        if not is_running:
            self.run_player()
        else:
            if is_paused:
                self.send_ipc_cmd("RESUME")
            else:
                self.send_ipc_cmd("PAUSE")

    def send_ipc_cmd(self, cmd):
        try:
            with open(self.ipc_cmd_path, "w", encoding="utf-8") as f:
                f.write(cmd)
        except Exception as e:
            print(f"IPC Send Error: {e}")

    def check_player_status(self):
        if not os.path.exists(self.ipc_status_path): 
            # Offline
            self.btn_toggle.setText("▶")
            self.btn_toggle.setToolTip("啟動播放器")
            return
        
        try:
            import time
            with open(self.ipc_status_path, "r", encoding="utf-8") as f:
                status = json.load(f)
            
            # Check staleness
            if time.time() - status.get("timestamp", 0) > 3.0:
                self.btn_toggle.setText("▶")
                self.btn_toggle.setToolTip("啟動播放器 (OFFLINE)")
                # Clear active data
                for i in range(self.scene_list.count()):
                    self.scene_list.item(i).setData(Qt.UserRole, None)
                return

            active_idx = status.get("scene_idx", -1)
            remaining = float(status.get("remaining", 0))
            total = float(status.get("total", 10))
            is_paused = status.get("paused", False)
            
            # Update Toggle Button
            if is_paused:
                self.btn_toggle.setText("▶") # Resume
                self.btn_toggle.setToolTip("繼續播放")
            else:
                self.btn_toggle.setText("⏸") # Pause
                self.btn_toggle.setToolTip("暫停")

            # Update List Items
            for i in range(self.scene_list.count()):
                item = self.scene_list.item(i)
                original_name = self.config["playlist"][i].get("name", "Scene")
                item.setText(original_name) # Clean name
                
                if i == active_idx:
                    data = {
                        "is_active": True,
                        "remaining": remaining,
                        "total": total,
                        "is_paused": is_paused
                    }
                    item.setData(Qt.UserRole, data)
                    item.setForeground(Qt.white)
                else:
                    item.setData(Qt.UserRole, None)
                    item.setForeground(Qt.gray)
            
            self.scene_list.viewport().update() # Force redraw
                    
        except:
             pass

    def scale_all_zones(self, old_w, old_h, new_w, new_h):
        try:
            old_w = float(old_w)
            old_h = float(old_h)
            new_w = float(new_w)
            new_h = float(new_h)
            if not old_w or not old_h or not new_w or not new_h:
                return
            if int(old_w) == int(new_w) and int(old_h) == int(new_h):
                return
            sx = new_w / old_w
            sy = new_h / old_h
            for scene in self.config.get("playlist", []):
                for zone in scene.get("zones", []):
                    for key in ("x", "w"):
                        if key in zone:
                            zone[key] = int(round(float(zone.get(key, 0)) * sx))
                    for key in ("y", "h"):
                        if key in zone:
                            zone[key] = int(round(float(zone.get(key, 0)) * sy))
        except Exception as e:
            print(f"Scale zones failed: {e}")

    def detect_resolution(self):
        # Find the screen with the largest area (likely the display screen)
        # Use PHYSICAL resolution (Logical * DPR)
        max_area = 0
        target_size = (1920, 1080)
        
        for screen in QApplication.screens():
            size = screen.size()
            dpr = screen.devicePixelRatio()
            phys_w = int(size.width() * dpr)
            phys_h = int(size.height() * dpr)
            
            area = phys_w * phys_h
            if area > max_area:
                max_area = area
                target_size = (phys_w, phys_h)
        
        w, h = target_size
        
        if "global_settings" not in self.config:
            self.config["global_settings"] = {}

        old_w = self.config["global_settings"].get("screen_width", w)
        old_h = self.config["global_settings"].get("screen_height", h)
        self.scale_all_zones(old_w, old_h, w, h)
        
        self.config["global_settings"]["screen_width"] = w
        self.config["global_settings"]["screen_height"] = h
        self.lbl_res.setText(f"畫面: {w}x{h}")
        self.save_config()
        
        # If there is an active scene, refresh grid to use new resolution
        if self.scene_list.currentRow() >= 0:
            self.refresh_grid_ui()

    def load_config(self):
        self.config = config_manager.load_config()
        
        # Initialize defaults if missing
        if "global_settings" not in self.config:
            screen = QApplication.primaryScreen().size()
            self.config["global_settings"] = {
                "screen_width": screen.width(),
                "screen_height": screen.height()
            }
            
        if "playlist" not in self.config:
             self.config["playlist"] = []

        # Update UI
        sw = self.config["global_settings"].get("screen_width", 1920)
        sh = self.config["global_settings"].get("screen_height", 1080)
        self.lbl_res.setText(f"畫面: {sw}x{sh}")
        
        self.scene_list.clear()
        for scene in self.config["playlist"]:
            item = QListWidgetItem(scene.get("name", "Unnamed Scene"))
            item.setData(Qt.UserRole + 1, scene)
            self.scene_list.addItem(item)
        
        if self.config["playlist"]:
            self.scene_list.setCurrentRow(0)
        else:
            self.add_scene()

    def add_scene(self):
        new_scene = {
            "name": f"Scene {self.scene_list.count() + 1}",
            "duration": 10,
            "rows": 2,
            "cols": 2,
            "zones": [
                {"id": 0, "type": "web", "content": "https://google.com"},
                {"id": 1, "type": "web", "content": "https://bing.com"},
                {"id": 2, "type": "web", "content": "https://yahoo.com"},
                {"id": 3, "type": "web", "content": "https://duckduckgo.com"},
            ]
        }
        self.config["playlist"].append(new_scene)
        
        item = QListWidgetItem(new_scene["name"])
        item.setData(Qt.UserRole + 1, new_scene)
        self.scene_list.addItem(item)
        self.scene_list.setCurrentRow(self.scene_list.count() - 1)

    def sync_playlist_order(self):
        new_playlist = []
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            scene = item.data(Qt.UserRole + 1)
            if scene:
                new_playlist.append(scene)
        self.config["playlist"] = new_playlist
        config_manager.save_config(self.config)
        print("Auto-Saved (Order)")

    def on_scene_selected(self, current, previous):
        # 1. Save Previous (if valid and not deleted)
        if previous:
            prev_row = self.scene_list.row(previous)
            # Check if row is valid and within current config bounds
            # (It might be -1 if item was removed, or out of bounds if config changed)
            if prev_row >= 0 and prev_row < len(self.config["playlist"]):
                self.update_config_from_ui(target_row=prev_row)
                config_manager.save_config(self.config)
                print("Auto-Saved (Switch)")
        
        # 2. Load Current
        if current:
            curr_row = self.scene_list.row(current)
            if curr_row >= 0:
                self.load_scene_details(curr_row)

    def remove_scene(self):
        row = self.scene_list.currentRow()
        if row >= 0 and len(self.config["playlist"]) > 1:
            self.config["playlist"].pop(row)
            self.scene_list.takeItem(row)

    def load_scene_details(self, row):
        if row < 0 or row >= len(self.config["playlist"]):
            return
        
        scene = self.config["playlist"][row]
        
        self.edit_name.blockSignals(True)
        self.edit_name.setText(scene.get("name", ""))
        self.edit_name.blockSignals(False)
        
        self.edit_name.blockSignals(False)
        
        self.spin_duration.setValue(scene.get("duration", 10))
        
        self.spin_rows.blockSignals(True)
        self.spin_rows.setValue(int(scene.get("rows", 1)))
        self.spin_rows.blockSignals(False)
        
        self.spin_cols.blockSignals(True)
        self.spin_cols.setValue(int(scene.get("cols", 1)))
        self.spin_cols.blockSignals(False)
        
        self.render_zones_ui(scene)

    def update_scene_name_display(self, text):
        row = self.scene_list.currentRow()
        item = self.scene_list.item(row)
        if item:
            item.setText(text)
        if 0 <= row < len(self.config["playlist"]):
             self.config["playlist"][row]["name"] = text

    def auto_arrange(self):
        self.update_config_from_ui() # Commit current state first (e.g. text inputs)
        
        row = self.scene_list.currentRow()
        if row < 0: return
        scene = self.config["playlist"][row]
        
        rows = self.spin_rows.value()
        cols = self.spin_cols.value()
        
        sw = self.config.get("global_settings", {}).get("screen_width", 1920)
        sh = self.config.get("global_settings", {}).get("screen_height", 1080)
        
        zones = scene.get("zones", [])
        
        grid_w = sw // cols
        grid_h = sh // rows
        
        for i, zone in enumerate(zones):
            r = i // cols
            c = i % cols
            # Check if index is within grid bounds
            if i >= rows * cols: break 
            
            zone["x"] = c * grid_w
            zone["y"] = r * grid_h
            zone["w"] = grid_w
            zone["h"] = grid_h
            
        self.render_zones_ui(scene)
    
    def sync_zone0_size(self):
        self.update_config_from_ui() # Commit current drag state to config
        
        row = self.scene_list.currentRow()
        if row < 0: return
        scene = self.config["playlist"][row]
        zones = scene.get("zones", [])
        
        if not zones: return
        
        # Get Zone 0 size
        z0 = zones[0]
        w = z0.get("w", 100)
        h = z0.get("h", 100)
        
        changed = False
        for i in range(1, len(zones)):
            zones[i]["w"] = w
            zones[i]["h"] = h
            changed = True
            
        if changed:
            self.render_zones_ui(scene)

    def refresh_grid_ui(self):
        row = self.scene_list.currentRow()
        if row < 0: return
        scene = self.config["playlist"][row]
        
        rows = self.spin_rows.value()
        cols = self.spin_cols.value()
        scene["rows"] = rows
        scene["cols"] = cols
        
        target_count = rows * cols
        current_zones = scene.get("zones", [])
        
        # Adjust count
        if len(current_zones) < target_count:
            for i in range(len(current_zones), target_count):
                current_zones.append({"id": i, "type": "web", "content": ""})
        elif len(current_zones) > target_count:
             scene["zones"] = current_zones[:target_count]
            
        self.render_zones_ui(scene)

    def render_zones_ui(self, scene):
        while self.zones_layout.count():
            item = self.zones_layout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        
        self.zone_widgets = []
        zones = scene.get("zones", [])
        
        # Screen Dim
        sw = self.config.get("global_settings", {}).get("screen_width", 1920)
        sh = self.config.get("global_settings", {}).get("screen_height", 1080)
        
        rows = scene.get("rows", 1)
        cols = scene.get("cols", 1)
        grid_w = sw // cols
        grid_h = sh // rows
        if grid_w == 0: grid_w = 100
        if grid_h == 0: grid_h = 100

        # Clear Canvas Children
        for child in self.preview_canvas.children():
            if isinstance(child, QWidget):
                child.deleteLater()
        
        self.preview_btns = []

        for i, zone in enumerate(zones):
            # --- 1. Preview Widget (Absolute) ---
            # Use ResizableZone instead of QPushButton
            rz = ResizableZone(i, self.preview_canvas)
            self.preview_btns.append(rz)
            
            # --- 2. Zone Edit UI ---
            # Container for this zone
            frame = QGroupBox(f"Zone {i}")
            frame.setObjectName(f"zone_frame_{i}")
            frame.setStyleSheet(f"#zone_frame_{i} {{ border: 1px solid #777; margin-top: 5px; }}")
            
            vbox = QVBoxLayout(frame)
            
            # Handler for Zone Click (Selection)
            def on_zone_click(idx, r=rz, f=frame):
                # Deactivate others
                for other_rz in self.preview_btns:
                    if other_rz != r: other_rz.set_active(False)
                
                r.set_active(True)
                
                # Highlight UI Frame
                for w_dict in self.zone_widgets:
                    w_frame = w_dict.get("frame")
                    if w_frame:
                        obj_name = w_frame.objectName()
                        w_frame.setStyleSheet(f"#{obj_name} {{ border: 1px solid #777; margin-top: 5px; }}")
                
                f.setStyleSheet(f"#{f.objectName()} {{ border: 3px solid #4CAF50; margin-top: 5px; background-color: #2b2b2b; }}")
                self.right_panel.ensureWidgetVisible(f)

            rz.clicked.connect(on_zone_click)
            
            # Handler for Geometry Change (Drag/Resize)
            def on_zone_geo_change(idx, rect, r=rz):
                # Convert Preview Rect -> Logical Rect
                sw = self.config.get("global_settings", {}).get("screen_width", 1920)
                sh = self.config.get("global_settings", {}).get("screen_height", 1080)
                canvas_w = self.preview_canvas.width()
                canvas_h = self.preview_canvas.height()
                
                scale_x = sw / canvas_w if canvas_w else 1
                scale_y = sh / canvas_h if canvas_h else 1
                
                lx = int(rect.x() * scale_x)
                ly = int(rect.y() * scale_y)
                lw = int(rect.width() * scale_x)
                lh = int(rect.height() * scale_y)
                
                # Update Spinboxes (block signals to prevent loop)
                if idx < len(self.zone_widgets):
                    widgets = self.zone_widgets[idx]
                    widgets["x"].blockSignals(True); widgets["x"].setValue(lx); widgets["x"].blockSignals(False)
                    widgets["y"].blockSignals(True); widgets["y"].setValue(ly); widgets["y"].blockSignals(False)
                    widgets["w"].blockSignals(True); widgets["w"].setValue(lw); widgets["w"].blockSignals(False)
                    widgets["h"].blockSignals(True); widgets["h"].setValue(lh); widgets["h"].blockSignals(False)

            rz.geometry_changed.connect(on_zone_geo_change)

            # Row 1: Type + Content
            row1 = QHBoxLayout()
            combo = QComboBox()
            combo.addItems(["web", "exe", "video", "rdp"])
            combo.setCurrentText(zone.get("type", "web"))
            
            edit = QLineEdit()
            edit.setText(zone.get("content", ""))
            
            def on_text_changed(text, c=combo):
                t = text.lower().strip()
                if t.startswith("http"): c.setCurrentText("web")
                elif t.endswith(".exe"): c.setCurrentText("exe")
                elif t.endswith(".mp4"): c.setCurrentText("video")
                elif t.replace(".","").isdigit(): c.setCurrentText("rdp") 
            edit.textChanged.connect(on_text_changed)
            
            row1.addWidget(QLabel("類型:"))
            row1.addWidget(combo)
            row1.addWidget(QLabel("內容:"))
            row1.addWidget(edit)
            vbox.addLayout(row1)
            
            # Row 2: Advanced (Geometry)
            row2 = QHBoxLayout()
            row2.addWidget(QLabel("位置調整 (x, y, w, h):"))
            
            def_x = (i % cols) * grid_w
            def_y = (i // cols) * grid_h
            
            spin_x = QSpinBox(); spin_x.setRange(0, 9999); spin_x.setValue(zone.get("x", def_x))
            spin_y = QSpinBox(); spin_y.setRange(0, 9999); spin_y.setValue(zone.get("y", def_y))
            spin_w = QSpinBox(); spin_w.setRange(0, 9999); spin_w.setValue(zone.get("w", grid_w))
            spin_h = QSpinBox(); spin_h.setRange(0, 9999); spin_h.setValue(zone.get("h", grid_h))
            
            # Smart Handler binding
            def make_handler(index):
                return lambda: self.update_preview_and_resolve(index)
            handler = make_handler(i)
            
            spin_x.valueChanged.connect(handler)
            spin_y.valueChanged.connect(handler)
            spin_w.valueChanged.connect(handler)
            spin_h.valueChanged.connect(handler)

            row2.addWidget(spin_x)
            row2.addWidget(spin_y)
            row2.addWidget(spin_w)
            row2.addWidget(spin_h)
            vbox.addLayout(row2)
            
            # Row 3: Options
            # Row 3: Options
            row3 = QHBoxLayout()
            chk_strip = QCheckBox("強制去邊框")
            chk_strip.setChecked(zone.get("strip_border", True))
            chk_isolate_webview = QCheckBox("獨立 WebView")
            chk_isolate_webview.setToolTip("僅建議特殊網頁使用；切換場景時會關閉該 WebView，下次回來重新啟動。")
            chk_isolate_webview.setChecked(zone.get("isolate_webview", False))
            
            row3.addWidget(chk_strip)
            row3.addWidget(chk_isolate_webview)
            # [Refactor] Removed 'Use Edge' option (Internal WebView2 is now default)
            # chk_ext = QCheckBox("使用外部瀏覽器") ...
            # row3.addWidget(chk_ext)
            # row3.addWidget(combo_browser)
            vbox.addLayout(row3)
            
            self.zones_layout.addRow(frame)
            
            self.zone_widgets.append({
                "combo": combo, "edit": edit, 
                "x": spin_x, "y": spin_y, "w": spin_w, "h": spin_h,
                "strip": chk_strip,
                "isolate_webview": chk_isolate_webview, # "ext": chk_ext, "browser": combo_browser,
                "frame": frame
            })
            
        self.update_preview_from_widgets()

    def update_preview_from_widgets(self):
        sw = self.config.get("global_settings", {}).get("screen_width", 1920)
        sh = self.config.get("global_settings", {}).get("screen_height", 1080)
        canvas_w = self.preview_canvas.width()
        canvas_h = self.preview_canvas.height()
        scale_x = canvas_w / sw if sw else 1
        scale_y = canvas_h / sh if sh else 1
        
        for i, widgets in enumerate(self.zone_widgets):
            if i >= len(self.preview_btns): break
            x = widgets["x"].value()
            y = widgets["y"].value()
            w = widgets["w"].value()
            h = widgets["h"].value()
            self.preview_btns[i].setGeometry(int(x * scale_x), int(y * scale_y), int(w * scale_x), int(h * scale_y))
            self.preview_btns[i].show()

    def update_preview_and_resolve(self, changed_idx):
        if changed_idx >= len(self.zone_widgets): return
        widgets = self.zone_widgets[changed_idx]
        cx = widgets["x"].value(); cy = widgets["y"].value()
        cw = widgets["w"].value(); ch = widgets["h"].value()
        cr = cx + cw; cb = cy + ch
        
        # Prevent self negative
        if cw < 0: widgets["w"].setValue(1)
        if ch < 0: widgets["h"].setValue(1)
        
        # NOTE: Reduced logic for collision - previously was aggressive push
        # We now trust manual overlap is intended or handled by visual feedback.
        # Removing collision resolution logic here to fix "jumping" complaints 
        # when typing manually.
        pass
        
        self.update_preview_from_widgets()

    def update_config_from_ui(self, target_row=None):
        if target_row is not None:
            row = target_row
        else:
            row = self.scene_list.currentRow()
            
        if row >= 0 and row < len(self.config["playlist"]):
            scene = self.config["playlist"][row]
            scene["duration"] = self.spin_duration.value()
            scene["rows"] = self.spin_rows.value()
            scene["cols"] = self.spin_cols.value()
            
            target_count = scene["rows"] * scene["cols"]
            # Enforce zones count logic if needed, but safe to just update existing
            
            for i, widgets in enumerate(self.zone_widgets):
                if i < len(scene["zones"]):
                    z = scene["zones"][i]
                    z["type"] = widgets["combo"].currentText()
                    z["content"] = widgets["edit"].text().strip()
                    z["x"] = widgets["x"].value()
                    z["y"] = widgets["y"].value()
                    z["w"] = widgets["w"].value()
                    z["h"] = widgets["h"].value()
                    z["strip_border"] = widgets["strip"].isChecked()
                    z["isolate_webview"] = widgets["isolate_webview"].isChecked()
                    z["w"] = widgets["w"].value()
                    z["h"] = widgets["h"].value()
                    z["strip_border"] = widgets["strip"].isChecked()
                    z["isolate_webview"] = widgets["isolate_webview"].isChecked()
                    # z["use_edge"] = widgets["ext"].isChecked()
                    # z["browser_type"] = "edge" if widgets["browser"].currentIndex() == 1 else "chrome"

    def save_config(self):
        self.update_config_from_ui()
        config_manager.save_config(self.config)
        print("Config Saved")

if __name__ == "__main__":
    # Enable High DPI Support
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    window = AdminWindow()
    window.show()
    sys.exit(app.exec())
