import sys
import os
import re
import json
import time
import math
import zipfile
import webbrowser
import subprocess
import requests
from bs4 import BeautifulSoup
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QPushButton, QLineEdit, QLabel, QTextEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QComboBox,
    QSplitter, QListWidget, QMessageBox, QProgressBar, QGroupBox, QInputDialog,
    QDialog, QRadioButton, QDialogButtonBox, QMenu
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRect, QPoint, QEvent
from PySide6.QtGui import (
    QColor, QFont, QTextCharFormat, QSyntaxHighlighter, QPainter, QPen,
    QBrush, QPolygon, QPainterPath, QAction
)

CONFIG_FILE = "github_manager_config.json"

# 已綁定你的專屬 GitHub OAuth App Client ID
GITHUB_CLIENT_ID = "Ov23li5X4jMhFlXTzoRS"


def get_system_git_credential():
    """嘗試從 Windows 內建 Git Credential Manager 自動讀取已登入的 GitHub Token"""
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            startupinfo=startupinfo
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("password="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def ensure_safe_gitignore(repo_path):
    """自動建立或更新 .gitignore，防止敏感 Token 設定檔與快取被意外上傳"""
    gi_path = os.path.join(repo_path, ".gitignore")
    required_rules = ["github_manager_config.json", "__pycache__/", "*.pyc", ".env"]
    existing = ""
    if os.path.exists(gi_path):
        try:
            with open(gi_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except Exception:
            pass

    lines_to_add = [r for r in required_rules if r not in existing]
    if lines_to_add:
        with open(gi_path, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("# 自動安全防護：排除敏感登入設定與暫存檔\n")
            for r in lines_to_add:
                f.write(r + "\n")


# ==========================================
# 0. 智慧伸縮多行提交說明輸入框 (支援 Enter 換行 + 滑鼠移入展開 / 移出縮回並提示)
# ==========================================
class ExpandableCommitInput(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.collapsed_height = 30
        self.expanded_height = 95

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("輸入本次提交修改說明 (按 Enter 可換行，滑鼠移入自動展開)...")
        self.editor.setToolTip("支援多行輸入（按 Enter 換行）；打完字滑鼠移開會自動縮回省空間，滑鼠移入或點擊可隨時展開修改。")
        self.editor.setFixedHeight(self.collapsed_height)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.editor.setStyleSheet("""
            QTextEdit {
                border: 1px solid #8ab4f8;
                border-radius: 4px;
                padding: 3px 6px;
                background-color: #ffffff;
                color: #1f2328;
            }
            QTextEdit:focus {
                border: 2px solid #1f6feb;
            }
        """)
        self.editor.textChanged.connect(self._update_hint_badge)
        self.editor.installEventFilter(self)

        # 縮回狀態時顯示的「多行提示徽章」
        self.hint_badge = QLabel("")
        self.hint_badge.setVisible(False)
        self.hint_badge.setStyleSheet("""
            QLabel {
                background-color: #fff3cd;
                color: #856404;
                border: 1px solid #ffeeba;
                border-radius: 4px;
                padding: 2px 8px;
                font-weight: bold;
            }
        """)
        self.hint_badge.setToolTip("滑鼠移入或點擊左側輸入框即可展開檢視與修改完整多行說明")

        layout.addWidget(self.editor, stretch=1)
        layout.addWidget(self.hint_badge)

    def eventFilter(self, obj, event):
        if obj is self.editor:
            if event.type() in (QEvent.Enter, QEvent.FocusIn):
                self._expand()
            elif event.type() == QEvent.Leave:
                # 若滑鼠離開且目前沒有正在輸入焦點，則自動縮回
                if not self.editor.hasFocus():
                    self._collapse()
            elif event.type() == QEvent.FocusOut:
                # 延遲確認滑鼠是否也已離開元件再縮回
                QTimer.singleShot(120, self._check_collapse_on_blur)
        return super().eventFilter(obj, event)

    def _check_collapse_on_blur(self):
        if not self.editor.hasFocus() and not self.underMouse():
            self._collapse()

    def _expand(self):
        self.editor.setFixedHeight(self.expanded_height)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.hint_badge.setVisible(False)

    def _collapse(self):
        self.editor.setFixedHeight(self.collapsed_height)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 縮回時捲動回第一行，並更新後方提示標籤
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.editor.setTextCursor(cursor)
        self._update_hint_badge()

    def _update_hint_badge(self):
        # 只有在「縮回狀態」且「有輸入文字」時判斷是否顯示後方提示
        if self.editor.height() > self.collapsed_height:
            self.hint_badge.setVisible(False)
            return

        txt = self.editor.toPlainText()
        if not txt.strip():
            self.hint_badge.setVisible(False)
            return

        lines = txt.splitlines()
        if len(lines) > 1:
            self.hint_badge.setText(f"... (共 {len(lines)} 行，移入展開)")
            self.hint_badge.setVisible(True)
        elif len(txt) > 38:
            self.hint_badge.setText(f"... (共 {len(txt)} 字，移入展開)")
            self.hint_badge.setVisible(True)
        else:
            self.hint_badge.setVisible(False)

    def text(self):
        return self.editor.toPlainText()

    def clear(self):
        self.editor.clear()
        self._collapse()


# ==========================================
# 1. 遊戲式互動新手引導遮罩 (聚光燈 + 彈跳紅箭頭 + Q版精靈對話框)
# ==========================================
class GameTutorialOverlay(QWidget):
    def __init__(self, main_win, topic_name, steps):
        super().__init__(main_win)
        self.main_win = main_win
        self.topic_name = topic_name
        self.steps = steps
        self.current_idx = 0
        self.bounce_phase = 0.0
        self.target_widget = None

        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setGeometry(main_win.rect())
        main_win.installEventFilter(self)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._on_tick)
        self.anim_timer.start(30)

        self.dialog_box = QWidget(self)
        self.dialog_box.setStyleSheet("""
            QWidget#DialogCard {
                background-color: #f8f5ec;
                border: 3px solid #8c6239;
                border-radius: 14px;
            }
        """)
        self.dialog_box.setObjectName("DialogCard")

        card_layout = QHBoxLayout(self.dialog_box)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(14)

        self.avatar_lbl = QLabel("🧚‍♀️")
        self.avatar_lbl.setFixedSize(64, 64)
        self.avatar_lbl.setAlignment(Qt.AlignCenter)
        self.avatar_lbl.setFont(QFont("Segoe UI Emoji", 32))
        self.avatar_lbl.setStyleSheet("""
            background-color: #fff3cd;
            border: 3px solid #d4a017;
            border-radius: 32px;
        """)
        card_layout.addWidget(self.avatar_lbl)

        right_col = QVBoxLayout()
        self.step_title_lbl = QLabel()
        self.step_title_lbl.setFont(QFont("Microsoft JhengHei", 11, QFont.Bold))
        self.step_title_lbl.setStyleSheet("color: #7c3aed; border: none; background: transparent;")

        self.msg_lbl = QLabel()
        self.msg_lbl.setFont(QFont("Microsoft JhengHei", 10))
        self.msg_lbl.setWordWrap(True)
        self.msg_lbl.setStyleSheet("color: #2b2118; border: none; background: transparent;")

        btn_row = QHBoxLayout()
        self.btn_prev = QPushButton("◀ 上一步")
        self.btn_next = QPushButton("下一步 ▶")
        self.btn_skip = QPushButton("結束教學")

        for b in (self.btn_prev, self.btn_skip):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background-color: #e6d5b8; color: #3b2f2f;
                    border: 1px solid #8c6239; border-radius: 6px;
                    padding: 4px 12px; font-weight: bold;
                }
                QPushButton:hover { background-color: #d4be98; }
            """)
        self.btn_next.setCursor(Qt.PointingHandCursor)
        self.btn_next.setStyleSheet("""
            QPushButton {
                background-color: #238636; color: white;
                border: 1px solid #196127; border-radius: 6px;
                padding: 4px 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #2ea043; }
        """)

        self.btn_prev.clicked.connect(self.prev_step)
        self.btn_next.clicked.connect(self.next_step)
        self.btn_skip.clicked.connect(self.finish_tutorial)

        btn_row.addWidget(self.btn_prev)
        btn_row.addWidget(self.btn_next)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_skip)

        right_col.addWidget(self.step_title_lbl)
        right_col.addWidget(self.msg_lbl)
        right_col.addLayout(btn_row)
        card_layout.addLayout(right_col)

        self._apply_step()

    def eventFilter(self, obj, event):
        if obj is self.main_win and event.type() == QEvent.Resize:
            self.setGeometry(self.main_win.rect())
            self._position_dialog()
        return super().eventFilter(obj, event)

    def _on_tick(self):
        self.bounce_phase += 0.18
        self.update()

    def _get_target_rect(self):
        if not self.target_widget or not self.target_widget.isVisible():
            return QRect()
        top_left = self.target_widget.mapTo(self.main_win, QPoint(0, 0))
        return QRect(top_left, self.target_widget.size()).adjusted(-5, -5, 5, 5)

    def _apply_step(self):
        step = self.steps[self.current_idx]
        if "before_show" in step and callable(step["before_show"]):
            step["before_show"]()

        w_or_fn = step.get("widget")
        self.target_widget = w_or_fn() if callable(w_or_fn) else w_or_fn

        total = len(self.steps)
        self.step_title_lbl.setText(f"【{self.topic_name}】({self.current_idx + 1}/{total}) {step['title']}")
        self.msg_lbl.setText(step["desc"])

        self.btn_prev.setEnabled(self.current_idx > 0)
        self.btn_next.setText("完成本篇教學 🎉" if self.current_idx == total - 1 else "下一步 ▶")
        self._position_dialog()
        self.update()

    def _position_dialog(self):
        card_w = min(660, self.width() - 40)
        card_h = 165
        self.dialog_box.setFixedSize(card_w, card_h)

        t_rect = self._get_target_rect()
        x = (self.width() - card_w) // 2
        if not t_rect.isNull() and t_rect.center().y() < self.height() * 0.45:
            y = min(self.height() - card_h - 30, t_rect.bottom() + 95)
        else:
            y = max(45, (t_rect.top() - card_h - 95) if not t_rect.isNull() else 80)
        self.dialog_box.move(x, y)

    def next_step(self):
        if self.current_idx < len(self.steps) - 1:
            self.current_idx += 1
            self._apply_step()
        else:
            self.finish_tutorial()

    def prev_step(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self._apply_step()

    def finish_tutorial(self):
        self.anim_timer.stop()
        self.main_win.removeEventFilter(self)
        self.main_win.on_tutorial_finished()
        self.deleteLater()

    def mousePressEvent(self, event):
        t_rect = self._get_target_rect()
        if not t_rect.isNull() and t_rect.contains(event.pos()) and self.target_widget:
            if isinstance(self.target_widget, QPushButton) and self.target_widget.isEnabled():
                self.target_widget.click()
            QTimer.singleShot(250, self.next_step)
        else:
            event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        full_path = QPainterPath()
        full_path.addRect(self.rect())

        t_rect = self._get_target_rect()
        if not t_rect.isNull():
            hole_path = QPainterPath()
            hole_path.addRoundedRect(t_rect, 8, 8)
            full_path = full_path.subtracted(hole_path)

        painter.fillPath(full_path, QColor(0, 0, 0, 145))

        if not t_rect.isNull():
            glow_pen = QPen(QColor("#ffd700"), 3)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(t_rect, 8, 8)

            offset = int(math.sin(self.bounce_phase) * 8)
            cx = t_rect.center().x()

            if t_rect.top() > 90:
                tip_y = t_rect.top() - 8 + offset
                arrow = QPolygon([
                    QPoint(cx, tip_y),
                    QPoint(cx - 18, tip_y - 22),
                    QPoint(cx - 8, tip_y - 22),
                    QPoint(cx - 8, tip_y - 46),
                    QPoint(cx + 8, tip_y - 46),
                    QPoint(cx + 8, tip_y - 22),
                    QPoint(cx + 18, tip_y - 22),
                ])
            else:
                tip_y = t_rect.bottom() + 8 - offset
                arrow = QPolygon([
                    QPoint(cx, tip_y),
                    QPoint(cx - 18, tip_y + 22),
                    QPoint(cx - 8, tip_y + 22),
                    QPoint(cx - 8, tip_y + 46),
                    QPoint(cx + 8, tip_y + 46),
                    QPoint(cx + 8, tip_y + 22),
                    QPoint(cx + 18, tip_y + 22),
                ])

            painter.setPen(QPen(QColor("#ffffff"), 2.5))
            painter.setBrush(QBrush(QColor("#ff3b30")))
            painter.drawPolygon(arrow)


# ==========================================
# 2. 新建雲端倉庫並自動綁定推送對話框
# ==========================================
class CreateCloudRepoDialog(QDialog):
    def __init__(self, default_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("☁️ 新建 GitHub 雲端倉庫並自動綁定")
        self.setFixedSize(430, 230)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("程式將自動在你的 GitHub 建立遠端倉庫，並把左側本機資料夾綁定上傳："))

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("雲端倉庫名稱 (英文/數字):"))
        self.name_input = QLineEdit(default_name)
        self.name_input.setPlaceholderText("例如: my-python-tool")
        name_row.addWidget(self.name_input)
        layout.addLayout(name_row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("專案簡介 (選填):"))
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("簡短說明這個專案的用途...")
        desc_row.addWidget(self.desc_input)
        layout.addLayout(desc_row)

        vis_row = QHBoxLayout()
        self.rb_public = QRadioButton("公開 (Public - 所有人可見)")
        self.rb_private = QRadioButton("私有 (Private - 僅自己可見)")
        self.rb_public.setChecked(True)
        vis_row.addWidget(self.rb_public)
        vis_row.addWidget(self.rb_private)
        layout.addLayout(vis_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("立即建立並推送上雲")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ==========================================
# 3. Diff 紅綠語法高亮器
# ==========================================
class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_format = QTextCharFormat()
        self.add_format.setBackground(QColor("#1e3a29"))
        self.add_format.setForeground(QColor("#7ee787"))

        self.del_format = QTextCharFormat()
        self.del_format.setBackground(QColor("#491d22"))
        self.del_format.setForeground(QColor("#ffa198"))

        self.hunk_format = QTextCharFormat()
        self.hunk_format.setForeground(QColor("#79c0ff"))
        self.hunk_format.setFontWeight(QFont.Bold)

    def highlightBlock(self, text):
        if text.startswith("+") and not text.startswith("+++"):
            self.setFormat(0, len(text), self.add_format)
        elif text.startswith("-") and not text.startswith("---"):
            self.setFormat(0, len(text), self.del_format)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.hunk_format)


# ==========================================
# 4. 背景執行緒
# ==========================================
class DeviceOAuthWorker(QThread):
    code_ready = Signal(str, str)
    auth_finished = Signal(bool, str)

    def __init__(self, client_id):
        super().__init__()
        self.client_id = client_id.strip()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        headers = {"Accept": "application/json", "User-Agent": "GitHubManagerPro"}
        try:
            res = requests.post(
                "https://github.com/login/device/code",
                headers=headers,
                data={"client_id": self.client_id, "scope": "repo read:user"},
                timeout=15
            )
            if res.status_code != 200:
                self.auth_finished.emit(False, f"無法啟動瀏覽器驗證 ({res.status_code}): {res.text}")
                return

            data = res.json()
            device_code = data.get("device_code")
            user_code = data.get("user_code")
            verify_uri = data.get("verification_uri", "https://github.com/login/device")
            interval = int(data.get("interval", 5))
            expires_in = int(data.get("expires_in", 900))

            if not device_code or not user_code:
                self.auth_finished.emit(False, f"GitHub 回傳異常:\n{data}")
                return

            self.code_ready.emit(user_code, verify_uri)

            deadline = time.time() + expires_in
            while time.time() < deadline and not self._cancelled:
                time.sleep(interval)
                if self._cancelled:
                    return

                poll = requests.post(
                    "https://github.com/login/oauth/access_token",
                    headers=headers,
                    data={
                        "client_id": self.client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
                    },
                    timeout=15
                )
                p_data = poll.json()
                if "access_token" in p_data:
                    self.auth_finished.emit(True, p_data["access_token"])
                    return

                err = p_data.get("error", "")
                if err == "authorization_pending":
                    continue
                elif err == "slow_down":
                    interval += 5
                elif err in ("expired_token", "access_denied"):
                    self.auth_finished.emit(False, f"授權已取消或逾時 ({err})")
                    return

            if not self._cancelled:
                self.auth_finished.emit(False, "驗證逾時，請重新點擊登入。")
        except Exception as e:
            self.auth_finished.emit(False, f"OAuth 連線異常: {e}")


class CreateRemoteRepoWorker(QThread):
    created = Signal(bool, str, str)

    def __init__(self, token, name, description, is_private):
        super().__init__()
        self.token = token.strip()
        self.name = name.strip()
        self.description = description.strip()
        self.is_private = is_private

    def run(self):
        if not self.token:
            self.created.emit(False, "請先登入 GitHub 帳號才能建立雲端倉庫！", "")
            return
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.token}",
            "User-Agent": "GitHubManagerPro"
        }
        payload = {
            "name": self.name,
            "description": self.description,
            "private": self.is_private,
            "auto_init": False
        }
        try:
            r = requests.post("https://api.github.com/user/repos", headers=headers, json=payload, timeout=15)
            if r.status_code in (200, 201):
                data = r.json()
                self.created.emit(True, data["clone_url"], data["full_name"])
            else:
                msg = r.json().get("message", r.text)
                errors = r.json().get("errors", [])
                detail = errors[0].get("message", "") if errors else ""
                self.created.emit(False, f"建立失敗 ({r.status_code}): {msg} {detail}", "")
        except Exception as e:
            self.created.emit(False, f"連線異常: {e}", "")


class GitWorker(QThread):
    finished = Signal(bool, str, str)

    def __init__(self, repo_path, args, tag=""):
        super().__init__()
        self.repo_path = repo_path
        self.args = args
        self.tag = tag

    def run(self):
        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            proc = subprocess.run(
                ["git"] + self.args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo
            )
            output = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
            self.finished.emit(proc.returncode == 0, self.tag, output)
        except Exception as e:
            self.finished.emit(False, self.tag, str(e))


class AuthAndCloudWorker(QThread):
    auth_result = Signal(bool, dict, list, str)

    def __init__(self, token):
        super().__init__()
        self.token = token.strip()

    def run(self):
        if not self.token:
            self.auth_result.emit(False, {}, [], "未提供 Token")
            return
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.token}",
            "User-Agent": "GitHubManagerPro"
        }
        try:
            u_resp = requests.get("https://api.github.com/user", headers=headers, timeout=12)
            if u_resp.status_code != 200:
                self.auth_result.emit(False, {}, [], f"驗證失敗 ({u_resp.status_code}): {u_resp.json().get('message', '')}")
                return
            user_info = u_resp.json()

            r_resp = requests.get(
                "https://api.github.com/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator",
                headers=headers, timeout=15
            )
            repos = r_resp.json() if r_resp.status_code == 200 else []
            self.auth_result.emit(True, user_info, repos, "")
        except Exception as e:
            self.auth_result.emit(False, {}, [], f"連線異常: {e}")


class RemoteRepoDetailWorker(QThread):
    detail_ready = Signal(str, str)

    def __init__(self, full_name, token=""):
        super().__init__()
        self.full_name = full_name
        self.token = token.strip()

    def run(self):
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "GitHubManagerPro"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        try:
            repo_r = requests.get(f"https://api.github.com/repos/{self.full_name}", headers=headers, timeout=10)
            if repo_r.status_code != 200:
                self.detail_ready.emit(self.full_name, f"無法取得遠端資訊 ({repo_r.status_code})，可能為私有庫或尚未推送至 GitHub。")
                return
            info = repo_r.json()
            default_br = info.get("default_branch", "main")

            commits_r = requests.get(
                f"https://api.github.com/repos/{self.full_name}/commits?per_page=5", headers=headers, timeout=10
            )
            commits_txt = ""
            if commits_r.status_code == 200:
                for c in commits_r.json():
                    sha = c["sha"][:7]
                    msg = c["commit"]["message"].splitlines()[0]
                    date = c["commit"]["author"]["date"][:10]
                    commits_txt += f"  • [{sha}] ({date}) {msg}\n"

            contents_r = requests.get(
                f"https://api.github.com/repos/{self.full_name}/contents", headers=headers, timeout=10
            )
            files_txt = ""
            if contents_r.status_code == 200 and isinstance(contents_r.json(), list):
                items = sorted(contents_r.json(), key=lambda x: (x["type"] != "dir", x["name"].lower()))
                for item in items:
                    icon = "📁" if item["type"] == "dir" else "📄"
                    size_str = f" ({item['size']} B)" if item["type"] == "file" else ""
                    files_txt += f"  {icon} {item['name']}{size_str}\n"

            summary = (
                f"雲端儲存庫: {info['full_name']} ({'私有 Private' if info['private'] else '公開 Public'})\n"
                f"預設版本: {default_br}  |  Stars: {info['stargazers_count']}  |  最後推送: {info.get('pushed_at', '')[:19].replace('T', ' ')}\n"
                f"網址: {info['html_url']}\n"
                f"{'-'*55}\n"
                f"【雲端最新提交紀錄】\n{commits_txt or '  (無紀錄)'}\n"
                f"【雲端根目錄檔案結構】\n{files_txt or '  (空儲存庫)'}"
            )
            self.detail_ready.emit(self.full_name, summary)
        except Exception as e:
            self.detail_ready.emit(self.full_name, f"讀取雲端詳情失敗: {e}")


class ApiSearchWorker(QThread):
    result_ready = Signal(list, str)

    def __init__(self, mode, query_or_url, token=""):
        super().__init__()
        self.mode = mode
        self.target = query_or_url.strip()
        self.token = token.strip()

    def _headers(self):
        h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "GitHubManagerPro"}
        if self.token:
            h["Authorization"] = f"token {self.token}"
        return h

    def run(self):
        try:
            repos = []
            if self.mode == "search":
                url = "https://api.github.com/search/repositories"
                params = {"q": self.target, "sort": "stars", "order": "desc", "per_page": 30}
                resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
                if resp.status_code != 200:
                    self.result_ready.emit([], f"API 錯誤 ({resp.status_code}): {resp.json().get('message', '')}")
                    return
                for item in resp.json().get("items", []):
                    repos.append({
                        "full_name": item["full_name"],
                        "default_branch": item.get("default_branch", "main"),
                        "stars": item.get("stargazers_count", 0),
                        "description": item.get("description") or "無說明",
                        "html_url": item["html_url"]
                    })

            elif self.mode == "url":
                m_user = re.match(r"^https?://github\.com/([^/]+)/?$", self.target)
                m_repo = re.match(r"^https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$", self.target)

                if m_user:
                    owner = m_user.group(1)
                    api_url = f"https://api.github.com/users/{owner}/repos?per_page=100&sort=updated"
                    resp = requests.get(api_url, headers=self._headers(), timeout=15)
                    if resp.status_code == 200:
                        for item in resp.json():
                            repos.append({
                                "full_name": item["full_name"],
                                "default_branch": item.get("default_branch", "main"),
                                "stars": item.get("stargazers_count", 0),
                                "description": item.get("description") or "無說明",
                                "html_url": item["html_url"]
                            })
                    else:
                        self.result_ready.emit([], f"無法讀取帳號 {owner} 的儲存庫 ({resp.status_code})")
                        return
                else:
                    seen = set()
                    if m_repo:
                        full_target = f"{m_repo.group(1)}/{m_repo.group(2)}"
                        api_single = f"https://api.github.com/repos/{full_target}"
                        r_single = requests.get(api_single, headers=self._headers(), timeout=10)
                        if r_single.status_code == 200:
                            item = r_single.json()
                            seen.add(item["full_name"])
                            repos.append({
                                "full_name": item["full_name"],
                                "default_branch": item.get("default_branch", "main"),
                                "stars": item.get("stargazers_count", 0),
                                "description": f"【主專案】{item.get('description') or '無說明'}",
                                "html_url": item["html_url"]
                            })

                    resp = requests.get(self.target, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    ignore_owners = {"features", "enterprise", "topics", "collections", "events", "sponsors", "settings", "notifications", "login", "join", "about", "pricing"}
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if href.startswith("/"):
                            href = "https://github.com" + href
                        match = re.match(r"^https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$", href)
                        if match:
                            owner, repo_name = match.group(1), match.group(2)
                            if owner.lower() in ignore_owners or repo_name.endswith(".git"):
                                continue
                            full = f"{owner}/{repo_name}"
                            if full not in seen:
                                seen.add(full)
                                repos.append({
                                    "full_name": full,
                                    "default_branch": "main",
                                    "stars": "-",
                                    "description": a.get_text(strip=True) or "從頁面連結解析",
                                    "html_url": f"https://github.com/{full}"
                                })
            self.result_ready.emit(repos, "")
        except Exception as e:
            self.result_ready.emit([], f"執行異常: {str(e)}")


class BatchDownloadWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(str)

    def __init__(self, repo_list, save_dir, auto_extract=True, token=""):
        super().__init__()
        self.repo_list = repo_list
        self.save_dir = save_dir
        self.auto_extract = auto_extract
        self.token = token.strip()

    def run(self):
        total = len(self.repo_list)
        headers = {"User-Agent": "GitHubManagerPro"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        os.makedirs(self.save_dir, exist_ok=True)
        success_count = 0

        for idx, repo in enumerate(self.repo_list, start=1):
            full_name = repo["full_name"]
            safe_name = full_name.replace("/", "_")
            default_branch = repo.get("default_branch", "main")
            branches_to_try = list(dict.fromkeys([default_branch, "main", "master"]))

            downloaded = False
            for br in branches_to_try:
                zip_url = f"https://github.com/{full_name}/archive/refs/heads/{br}.zip"
                self.progress.emit(idx, total, f"[{idx}/{total}] 正在下載 {full_name} ({br})...")
                try:
                    r = requests.get(zip_url, headers=headers, stream=True, timeout=30)
                    if r.status_code == 200:
                        zip_path = os.path.join(self.save_dir, f"{safe_name}.zip")
                        with open(zip_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)

                        if self.auto_extract:
                            self.progress.emit(idx, total, f"[{idx}/{total}] 正在解壓縮 {safe_name}.zip...")
                            extract_target = os.path.join(self.save_dir, safe_name)
                            with zipfile.ZipFile(zip_path, "r") as zf:
                                zf.extractall(extract_target)
                            os.remove(zip_path)

                        downloaded = True
                        success_count += 1
                        break
                except Exception as e:
                    self.progress.emit(idx, total, f"錯誤 ({full_name}): {e}")

            if not downloaded:
                self.progress.emit(idx, total, f"[{idx}/{total}] 下載失敗: {full_name}")

        self.finished.emit(f"批次作業完成！成功處理 {success_count} / {total} 個儲存庫，存放於：\n{self.save_dir}")


# ==========================================
# 5. 瀏覽器驗證碼提示視窗
# ==========================================
class BrowserAuthDialog(QDialog):
    def __init__(self, user_code, verify_uri, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GitHub 瀏覽器網頁授權")
        self.setFixedSize(440, 230)
        self.verify_uri = verify_uri

        layout = QVBoxLayout(self)
        info = QLabel(
            "系統已自動開啟瀏覽器並將下方 8 碼驗證碼複製到剪貼簿。\n"
            "請在瀏覽器頁面貼上驗證碼並點擊授權（Authorize）："
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        code_lbl = QLabel(user_code)
        code_lbl.setAlignment(Qt.AlignCenter)
        code_lbl.setFont(QFont("Consolas", 22, QFont.Bold))
        code_lbl.setStyleSheet("background-color: #161b22; color: #58a6ff; padding: 12px; border-radius: 6px;")
        code_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(code_lbl)

        btn_row = QHBoxLayout()
        btn_reopen = QPushButton("重新開啟瀏覽器驗證頁")
        btn_reopen.setToolTip("若瀏覽器未自動跳出，點擊此鈕重新開啟 GitHub 驗證網頁")
        btn_reopen.clicked.connect(lambda: webbrowser.open(self.verify_uri))
        btn_cancel = QPushButton("取消等待")
        btn_cancel.setToolTip("中止本次瀏覽器登入授權流程")
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(btn_reopen)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)


# ==========================================
# 6. 共用元件：儲存庫表格與批次下載面板 (分頁 2 & 3)
# ==========================================
class RepoBatchPanel(QWidget):
    clone_requested = Signal(str)
    analyze_requested = Signal(str)

    def __init__(self, get_token_func, enable_double_click_analyze=False):
        super().__init__()
        self.get_token = get_token_func
        self.enable_double_click_analyze = enable_double_click_analyze
        self.repos_data = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["勾選", "儲存庫名稱 (Owner/Repo)", "預設版本", "Stars", "說明 / 來源"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        if self.enable_double_click_analyze:
            self.table.setToolTip("💡 提示：在任一專案列上「按滑鼠左鍵兩下」，可自動跳轉至「2. 網址頁面分析」深入解析該專案頁面！")
            self.table.cellDoubleClicked.connect(self._on_row_double_clicked)

        layout.addWidget(self.table)

        sel_layout = QHBoxLayout()
        btn_all = QPushButton("全選")
        btn_all.setToolTip("【用途】一次勾選上方表格中的所有專案。\n【使用時機】想把清單裡的全部專案一次打包下載時使用。")

        btn_none = QPushButton("取消全選")
        btn_none.setToolTip("【用途】取消目前所有已勾選的項目。\n【使用時機】想重新挑選少數幾個專案下載時使用。")

        btn_clone_sel = QPushButton("將選取列 Clone 至本機管理 (分頁1)")
        btn_clone_sel.setToolTip("【用途】將目前反白選取的專案完整下載並納入版本控制。\n【使用時機】打算修改此專案程式碼並長期追蹤版本變更時使用。")

        btn_all.clicked.connect(lambda: self._toggle_all(True))
        btn_none.clicked.connect(lambda: self._toggle_all(False))
        btn_clone_sel.clicked.connect(self._emit_clone)
        sel_layout.addWidget(btn_all)
        sel_layout.addWidget(btn_none)
        sel_layout.addWidget(btn_clone_sel)
        sel_layout.addStretch()
        layout.addLayout(sel_layout)

        dl_group = QGroupBox("批次打包下載與自動解壓縮")
        dl_layout = QHBoxLayout(dl_group)

        self.dir_input = QLineEdit(str(Path.home() / "Downloads" / "GitHub_Batch"))
        self.dir_input.setToolTip("設定批次下載 ZIP 壓縮檔與解壓縮後的本機存放資料夾路徑")

        btn_browse = QPushButton("選擇目錄...")
        btn_browse.setToolTip("【用途】開啟資料夾選擇視窗，指定下載存放路徑。\n【使用時機】不想存放在預設 Downloads 資料夾時使用。")
        btn_browse.clicked.connect(self._choose_dir)

        self.chk_extract = QCheckBox("下載後自動解壓縮並刪除 ZIP")
        self.chk_extract.setChecked(True)
        self.chk_extract.setToolTip("勾選後，每個專案下載完成會立即自動解壓縮成資料夾，並刪除原始 .zip 檔以節省空間")

        self.btn_download = QPushButton("開始批次下載勾選項目")
        self.btn_download.setStyleSheet("background-color: #238636; color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_download.setToolTip("【用途】將上方所有「已勾選」的儲存庫打包下載為 ZIP 並自動解壓縮。\n【使用時機】純粹想抓程式碼回來參考或執行，不需要版本紀錄時使用。")
        self.btn_download.clicked.connect(self._start_download)

        dl_layout.addWidget(QLabel("儲存位置:"))
        dl_layout.addWidget(self.dir_input)
        dl_layout.addWidget(btn_browse)
        dl_layout.addWidget(self.chk_extract)
        dl_layout.addWidget(self.btn_download)
        layout.addWidget(dl_group)

        self.progress_bar = QProgressBar()
        self.status_lbl = QLabel("就緒")
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_lbl)

    def _on_row_double_clicked(self, row, col):
        if 0 <= row < len(self.repos_data):
            url = self.repos_data[row]["html_url"]
            self.analyze_requested.emit(url)

    def populate(self, repos):
        self.repos_data = repos
        self.table.setRowCount(0)
        for row_idx, r in enumerate(repos):
            self.table.insertRow(row_idx)
            chk_widget = QWidget()
            chk_box = QCheckBox()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk_box)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)

            self.table.setCellWidget(row_idx, 0, chk_widget)
            self.table.setItem(row_idx, 1, QTableWidgetItem(r["full_name"]))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(r["default_branch"])))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(r["stars"])))
            self.table.setItem(row_idx, 4, QTableWidgetItem(r["description"]))

    def _toggle_all(self, state):
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w:
                w.findChild(QCheckBox).setChecked(state)

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "選擇下載存放資料夾", self.dir_input.text())
        if d:
            self.dir_input.setText(d)

    def _get_checked_repos(self):
        selected = []
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w and w.findChild(QCheckBox).isChecked():
                selected.append(self.repos_data[r])
        return selected

    def _emit_clone(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.repos_data):
            QMessageBox.information(self, "提示", "請先在表格中點選一個專案列。")
            return
        url = self.repos_data[row]["html_url"] + ".git"
        self.clone_requested.emit(url)

    def _start_download(self):
        targets = self._get_checked_repos()
        if not targets:
            QMessageBox.warning(self, "提示", "請先勾選至少一個儲存庫！")
            return
        self.btn_download.setEnabled(False)
        self.progress_bar.setMaximum(len(targets))
        self.progress_bar.setValue(0)

        self.dl_worker = BatchDownloadWorker(
            targets, self.dir_input.text(), self.chk_extract.isChecked(), self.get_token()
        )
        self.dl_worker.progress.connect(self._on_progress)
        self.dl_worker.finished.connect(self._on_finished)
        self.dl_worker.start()

    def _on_progress(self, cur, total, msg):
        self.progress_bar.setValue(cur)
        self.status_lbl.setText(msg)

    def _on_finished(self, summary):
        self.btn_download.setEnabled(True)
        self.status_lbl.setText("下載完成")
        QMessageBox.information(self, "完成", summary)


# ==========================================
# 7. 分頁一：雙欄對照工作台 (雙白框與雙黑窗皆完全水平對齊)
# ==========================================
class DualPaneGitTab(QWidget):
    cloud_repo_created = Signal()

    def __init__(self, config, save_config_func, get_token_func):
        super().__init__()
        self.config = config
        self.save_config = save_config_func
        self.get_token = get_token_func
        self.current_repo = self.config.get("last_repo", "")
        self.cloud_repos = []
        self.workers = []
        self._init_ui()

        if self.current_repo and os.path.isdir(os.path.join(self.current_repo, ".git")):
            self.repo_path_input.setText(self.current_repo)
            self.refresh_local_all()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        master_v_split = QSplitter(Qt.Vertical)

        # 上半部：左【本地操作與清單】 | 右【雲端倉庫清單】
        top_h_split = QSplitter(Qt.Horizontal)

        # --- 左上：本地站台控制區 ---
        left_top_group = QGroupBox("【本地站台】本機倉庫與版本控制")
        left_top_layout = QVBoxLayout(left_top_group)

        path_bar = QHBoxLayout()
        self.repo_path_input = QLineEdit()
        self.repo_path_input.setPlaceholderText("選擇本機專案資料夾...")
        self.repo_path_input.setToolTip("目前正在管理的本機專案資料夾路徑")

        self.btn_open = QPushButton("開啟")
        self.btn_open.setToolTip("【用途】選擇要管理的本機專案資料夾（若為新資料夾，會自動建立版控並產生安全 .gitignore）。\n【使用時機】切換不同專案，或把全新的本機資料夾納入管理時使用。")

        self.btn_refresh_local = QPushButton("重新整理")
        self.btn_refresh_local.setToolTip("【用途】重新掃描本機檔案變更狀態、版本清單與右側雲端同步資訊。\n【使用時機】在 VS Code 或其他編輯器修改存檔後，更新畫面狀態時使用。")

        self.btn_open.clicked.connect(self._browse_repo)
        self.btn_refresh_local.clicked.connect(self.refresh_local_all)
        path_bar.addWidget(self.repo_path_input)
        path_bar.addWidget(self.btn_open)
        path_bar.addWidget(self.btn_refresh_local)
        left_top_layout.addLayout(path_bar)

        br_bar = QHBoxLayout()
        self.branch_combo = QComboBox()
        self.branch_combo.setMinimumWidth(105)
        self.branch_combo.setToolTip("選擇專案的不同開發版本（例如主版本 main 或實驗用新版本）")

        self.btn_sw = QPushButton("切換版本")
        self.btn_sw.setToolTip("【用途】將本機資料夾的檔案內容切換為下拉選單所選的版本。\n【使用時機】需要在主版本與其他實驗版本之間來回切換測試時使用。")

        self.btn_new_br = QPushButton("建新版本")
        self.btn_new_br.setToolTip("【用途】從目前進度複製建立一個全新的獨立開發版本並自動切換過去。\n【使用時機】準備開發新功能或大幅修改程式，怕改壞原本穩定版本時使用。")

        self.btn_merge = QPushButton("合併版本")
        self.btn_merge.setToolTip("【用途】將下拉選單指定的版本內容，整合合併到你目前所在的版本中。\n【使用時機】新版本的功能測試無誤，準備併回主版本（main）正式發布時使用。")

        self.btn_pull = QPushButton("↓ Pull")
        self.btn_pull.setToolTip("【用途】從 GitHub 雲端下載最新的版本進度並更新到本機。\n【使用時機】在別台電腦改過程式並上傳後，回到這台電腦開工前務必先按一次。")

        self.btn_sync = QPushButton("↑ 一鍵提交並 Push")
        self.btn_sync.setStyleSheet("background-color: #1f6feb; color: white; font-weight: bold; padding: 4px 10px;")
        self.btn_sync.setToolTip("【用途】自動打包所有變更檔案並上傳同步至 GitHub 雲端（若為新資料夾尚未綁定雲端，會自動引導建立雲端倉庫並首次推送）。\n【使用時機】修改完程式並確認下方紅綠對比無誤後，收工備份與上傳時使用。")

        self.btn_sw.clicked.connect(self._switch_branch)
        self.btn_new_br.clicked.connect(self._create_branch)
        self.btn_merge.clicked.connect(self._merge_branch)
        self.btn_pull.clicked.connect(lambda: self._run_git(["pull"], "pull"))
        self.btn_sync.clicked.connect(self._one_click_commit_push)

        br_bar.addWidget(QLabel("版本:"))
        br_bar.addWidget(self.branch_combo)
        br_bar.addWidget(self.btn_sw)
        br_bar.addWidget(self.btn_new_br)
        br_bar.addWidget(self.btn_merge)
        br_bar.addStretch()
        br_bar.addWidget(self.btn_pull)
        br_bar.addWidget(self.btn_sync)
        left_top_layout.addLayout(br_bar)

        # 左右並排的雙白框（標題列等高 26px，確保紅框上下完全對齊）
        lists_row = QHBoxLayout()

        chg_box = QVBoxLayout()
        chg_hdr = QWidget()
        chg_hdr.setFixedHeight(26)
        chg_hdr_layout = QHBoxLayout(chg_hdr)
        chg_hdr_layout.setContentsMargins(0, 0, 0, 0)
        chg_hdr_layout.addWidget(QLabel("變更檔案 (點擊看 Diff):"))
        chg_box.addWidget(chg_hdr)

        self.changes_list = QListWidget()
        self.changes_list.setToolTip("列出本機有新增、修改或刪除的檔案；點擊任一檔名可在下方黑窗檢視紅綠差異對比")
        self.changes_list.itemClicked.connect(self._on_file_clicked)
        chg_box.addWidget(self.changes_list)

        hist_box = QVBoxLayout()
        hist_hdr = QWidget()
        hist_hdr.setFixedHeight(26)
        hist_hdr_layout = QHBoxLayout(hist_hdr)
        hist_hdr_layout.setContentsMargins(0, 0, 0, 0)
        hist_hdr_layout.addWidget(QLabel("本機提交歷史:"))
        self.btn_revert = QPushButton("還原此版")
        self.btn_revert.setFixedHeight(24)
        self.btn_revert.setStyleSheet("color: #f85149; padding: 1px 8px;")
        self.btn_revert.setToolTip("【用途】強制將本機專案所有檔案時光倒流回下方點選的歷史版本。\n【使用時機】程式改壞、改亂想全部放棄重來時使用（⚠️ 注意：該版之後未提交的修改會清空）。")
        self.btn_revert.clicked.connect(self._revert_selected_commit)
        hist_hdr_layout.addWidget(self.btn_revert)
        hist_box.addWidget(hist_hdr)

        self.history_list = QListWidget()
        self.history_list.setToolTip("顯示歷次存檔提交紀錄；點擊任一列可在下方黑窗檢視該次修改細節，或搭配「還原此版」回溯")
        self.history_list.itemClicked.connect(self._on_commit_clicked)
        hist_box.addWidget(self.history_list)

        lists_row.addLayout(chg_box, stretch=1)
        lists_row.addLayout(hist_box, stretch=1)
        left_top_layout.addLayout(lists_row, stretch=1)

        # 智慧伸縮提交說明輸入框（獨立置於雙白框正下方，避免擠壓左側白框高度）
        self.commit_msg_input = ExpandableCommitInput()
        left_top_layout.addWidget(self.commit_msg_input)

        # --- 右上：遠端站台倉庫清單 ---
        right_top_group = QGroupBox("【遠端站台】GitHub 雲端倉庫清單")
        right_top_layout = QVBoxLayout(right_top_group)

        cloud_top = QHBoxLayout()
        self.cloud_status_lbl = QLabel("狀態：尚未登入 GitHub 帳號")
        self.cloud_status_lbl.setStyleSheet("font-weight: bold; color: #d29922;")

        self.btn_create_cloud = QPushButton("☁️ 新建並綁定雲端倉庫")
        self.btn_create_cloud.setStyleSheet("background-color: #8957e5; color: white; font-weight: bold; padding: 4px 10px;")
        self.btn_create_cloud.setToolTip("【用途】直接在你的 GitHub 帳號建立一個全新的雲端倉庫，並自動綁定左側本機資料夾推上去。\n【使用時機】本機新建立的專案資料夾還沒在 GitHub 上建倉庫時使用（免開網頁、免打指令）。")
        self.btn_create_cloud.clicked.connect(self._open_create_cloud_repo_dialog)

        self.btn_clone_cloud = QPushButton("↙ Clone 選取倉庫至本機")
        self.btn_clone_cloud.setStyleSheet("background-color: #238636; color: white; font-weight: bold; padding: 4px 10px;")
        self.btn_clone_cloud.setToolTip("【用途】將下方選取的 GitHub 雲端倉庫完整下載到本機資料夾，並自動開啟於左側進行開發。\n【使用時機】換新電腦或想下載自己雲端上的專案回來修改時使用。")
        self.btn_clone_cloud.clicked.connect(self._clone_selected_cloud_repo)

        cloud_top.addWidget(self.cloud_status_lbl)
        cloud_top.addStretch()
        cloud_top.addWidget(self.btn_create_cloud)
        cloud_top.addWidget(self.btn_clone_cloud)
        right_top_layout.addLayout(cloud_top)

        self.cloud_repo_table = QTableWidget(0, 4)
        self.cloud_repo_table.setHorizontalHeaderLabels(["雲端儲存庫名稱", "權限", "預設版本", "最後更新"])
        self.cloud_repo_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.cloud_repo_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.cloud_repo_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.cloud_repo_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.cloud_repo_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.cloud_repo_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cloud_repo_table.setToolTip("登入後顯示帳號下所有公開與私有雲端倉庫；點選任一列可在下方黑窗檢視雲端檔案與最新紀錄")
        self.cloud_repo_table.itemClicked.connect(self._on_cloud_repo_clicked)
        right_top_layout.addWidget(self.cloud_repo_table)

        top_h_split.addWidget(left_top_group)
        top_h_split.addWidget(right_top_group)
        top_h_split.setSizes([600, 600])

        # 下半部：左【視覺化 Diff 黑窗】 | 右【雲端遠端狀態黑窗】 (完全等高對齊)
        bottom_h_split = QSplitter(Qt.Horizontal)

        left_bot_widget = QWidget()
        left_bot_layout = QVBoxLayout(left_bot_widget)
        left_bot_layout.setContentsMargins(4, 2, 4, 2)
        left_bot_layout.addWidget(QLabel("視覺化檔案變更對比 (Diff):"))
        self.diff_viewer = QTextEdit()
        self.diff_viewer.setReadOnly(True)
        self.diff_viewer.setFont(QFont("Consolas", 9))
        self.diff_viewer.setStyleSheet("background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d;")
        self.diff_viewer.setToolTip("綠色代表新增的程式碼，紅色代表刪除的程式碼")
        self.highlighter = DiffHighlighter(self.diff_viewer.document())
        left_bot_layout.addWidget(self.diff_viewer)

        right_bot_widget = QWidget()
        right_bot_layout = QVBoxLayout(right_bot_widget)
        right_bot_layout.setContentsMargins(4, 2, 4, 2)
        right_bot_layout.addWidget(QLabel("雲端遠端狀態與檔案結構預覽:"))
        self.cloud_detail_viewer = QTextEdit()
        self.cloud_detail_viewer.setReadOnly(True)
        self.cloud_detail_viewer.setFont(QFont("Consolas", 9))
        self.cloud_detail_viewer.setStyleSheet("background-color: #0d1117; color: #e6edf3; border: 1px solid #30363d;")
        self.cloud_detail_viewer.setPlaceholderText("登入帳號並開啟本機專案或點選上方雲端倉庫，即可即時檢視雲端最新提交與檔案目錄...")
        right_bot_layout.addWidget(self.cloud_detail_viewer)

        bottom_h_split.addWidget(left_bot_widget)
        bottom_h_split.addWidget(right_bot_widget)
        bottom_h_split.setSizes([600, 600])

        top_h_split.splitterMoved.connect(lambda pos, idx: bottom_h_split.setSizes(top_h_split.sizes()))
        bottom_h_split.splitterMoved.connect(lambda pos, idx: top_h_split.setSizes(bottom_h_split.sizes()))

        master_v_split.addWidget(top_h_split)
        master_v_split.addWidget(bottom_h_split)
        master_v_split.setSizes([350, 290])
        main_layout.addWidget(master_v_split)

        self.console_log = QTextEdit()
        self.console_log.setMaximumHeight(68)
        self.console_log.setReadOnly(True)
        self.console_log.setPlaceholderText("Git 與雲端同步日誌...")
        main_layout.addWidget(self.console_log)

    def update_cloud_account(self, logged_in, user_info=None, repos=None):
        if not logged_in:
            self.cloud_repos = []
            self.cloud_repo_table.setRowCount(0)
            self.cloud_status_lbl.setText("狀態：未登入")
            self.cloud_status_lbl.setStyleSheet("font-weight: bold; color: #f85149;")
            self.cloud_detail_viewer.clear()
            self._log("已登出 GitHub 帳號，右側雲端資訊已清空。")
            return

        login_name = user_info.get("login", "Unknown")
        self.cloud_repos = repos or []
        self.cloud_status_lbl.setText(f"已連線：{login_name} (共 {len(self.cloud_repos)} 個倉庫)")
        self.cloud_status_lbl.setStyleSheet("font-weight: bold; color: #3fb950;")

        self.cloud_repo_table.setRowCount(0)
        for idx, r in enumerate(self.cloud_repos):
            self.cloud_repo_table.insertRow(idx)
            self.cloud_repo_table.setItem(idx, 0, QTableWidgetItem(r["full_name"]))
            self.cloud_repo_table.setItem(idx, 1, QTableWidgetItem("私有" if r["private"] else "公開"))
            self.cloud_repo_table.setItem(idx, 2, QTableWidgetItem(r.get("default_branch", "main")))
            self.cloud_repo_table.setItem(idx, 3, QTableWidgetItem(r.get("updated_at", "")[:10]))

        self._log(f"成功登入 GitHub 帳號 [{login_name}]，已載入 {len(self.cloud_repos)} 個雲端儲存庫。")
        self._sync_right_pane_with_local_remote()

    def _on_cloud_repo_clicked(self, item):
        row = item.row()
        if 0 <= row < len(self.cloud_repos):
            self._fetch_cloud_detail(self.cloud_repos[row]["full_name"])

    def _fetch_cloud_detail(self, full_name):
        self.cloud_detail_viewer.setPlainText(f"正在從 GitHub 讀取 {full_name} 的雲端狀態...")
        w = RemoteRepoDetailWorker(full_name, self.get_token())
        w.detail_ready.connect(lambda name, txt: self.cloud_detail_viewer.setPlainText(txt))
        self.workers.append(w)
        w.start()

    def _sync_right_pane_with_local_remote(self):
        if not self.current_repo or not os.path.isdir(os.path.join(self.current_repo, ".git")):
            return
        self._run_git(["remote", "get-url", "origin"], "remote_url")

    def _clone_selected_cloud_repo(self):
        row = self.cloud_repo_table.currentRow()
        if row < 0 or row >= len(self.cloud_repos):
            QMessageBox.information(self, "提示", "請先在右側「雲端儲存庫名稱」表格中點選一個專案！")
            return
        self.prompt_clone(self.cloud_repos[row]["clone_url"])

    def _open_create_cloud_repo_dialog(self):
        if not self.get_token():
            QMessageBox.warning(self, "尚未登入", "請先點擊頂部「瀏覽器網頁一鍵登入」登入 GitHub 帳號！")
            return
        if not self.current_repo or not os.path.isdir(self.current_repo):
            QMessageBox.warning(self, "未開啟本機資料夾", "請先在左側點擊「開啟」選擇要上傳的本機專案資料夾！")
            return

        folder_name = os.path.basename(self.current_repo)
        safe_default = re.sub(r"[^A-Za-z0-9_.-]+", "-", folder_name).strip("-") or "my-project"

        dlg = CreateCloudRepoDialog(safe_default, self)
        if dlg.exec() != QDialog.Accepted:
            return

        repo_name = dlg.name_input.text().strip()
        if not repo_name:
            QMessageBox.warning(self, "錯誤", "雲端倉庫名稱不可為空！")
            return

        if not os.path.isdir(os.path.join(self.current_repo, ".git")):
            ensure_safe_gitignore(self.current_repo)
            subprocess.run(["git", "init", "-b", "main"], cwd=self.current_repo)

        self._log(f"正在 GitHub 雲端建立新倉庫 [{repo_name}]...")
        w = CreateRemoteRepoWorker(
            self.get_token(), repo_name, dlg.desc_input.text(), dlg.rb_private.isChecked()
        )
        w.created.connect(self._on_cloud_repo_created)
        self.workers.append(w)
        w.start()

    def _on_cloud_repo_created(self, ok, clone_url_or_err, full_name):
        if not ok:
            QMessageBox.critical(self, "建立雲端倉庫失敗", clone_url_or_err)
            return

        self._log(f"雲端倉庫建立成功：{full_name}，正在自動綁定本機並執行首次推送...")
        ensure_safe_gitignore(self.current_repo)
        self._run_git(["branch", "-M", "main"], "bind_step_branch|" + clone_url_or_err)

    def _log(self, text):
        self.console_log.append(text.strip())

    def _browse_repo(self):
        d = QFileDialog.getExistingDirectory(self, "選擇本機專案資料夾")
        if d:
            if not os.path.isdir(os.path.join(d, ".git")):
                ans = QMessageBox.question(
                    self, "初始化新專案版控",
                    "此資料夾尚未建立版本控制，是否立即初始化？\n（系統將自動建立安全的 .gitignore 保護您的登入設定檔）"
                )
                if ans == QMessageBox.Yes:
                    self.current_repo = d
                    self.repo_path_input.setText(d)
                    self.config["last_repo"] = d
                    self.save_config()
                    ensure_safe_gitignore(d)
                    self._run_git(["init", "-b", "main"], "init")
                return
            self.current_repo = d
            self.repo_path_input.setText(d)
            self.config["last_repo"] = d
            self.save_config()
            self.refresh_local_all()

    def prompt_clone(self, prefill_url=""):
        url, ok = QInputDialog.getText(self, "Clone GitHub 儲存庫", "請輸入 GitHub 倉庫網址 (.git):", text=prefill_url)
        if not ok or not url.strip():
            return
        parent_dir = QFileDialog.getExistingDirectory(self, "選擇要存放專案的本機父資料夾")
        if not parent_dir:
            return
        repo_name = url.strip().rstrip("/").split("/")[-1].replace(".git", "")
        target_path = os.path.join(parent_dir, repo_name)
        self._log(f"正在 Clone {url} 至 {target_path} ...")
        w = GitWorker(parent_dir, ["clone", url.strip()], f"clone|{target_path}")
        w.finished.connect(self._on_git_done)
        self.workers.append(w)
        w.start()

    def _run_git(self, args, tag):
        if not self.current_repo or not os.path.isdir(self.current_repo):
            return
        w = GitWorker(self.current_repo, args, tag)
        w.finished.connect(self._on_git_done)
        self.workers.append(w)
        w.start()

    def refresh_local_all(self):
        if not self.current_repo:
            return
        self._run_git(["status", "--short"], "status")
        self._run_git(["branch", "--list"], "branches")
        self._run_git(["log", "-n", "25", "--oneline"], "log")
        self._sync_right_pane_with_local_remote()

    def _on_git_done(self, success, tag, out):
        if tag.startswith("bind_step_branch|"):
            remote_url = tag.split("|", 1)[1]
            subprocess.run(["git", "remote", "remove", "origin"], cwd=self.current_repo, capture_output=True)
            self._run_git(["remote", "add", "origin", remote_url], "bind_step_add")
            return

        if tag == "bind_step_add":
            self._run_git(["add", "."], "bind_step_commit")
            return

        if tag == "bind_step_commit":
            msg = self.commit_msg_input.text().strip() or "Initial commit: 上傳本機專案至 GitHub"
            self._run_git(["commit", "-m", msg], "bind_step_push")
            return

        if tag == "bind_step_push":
            self.commit_msg_input.clear()
            self._run_git(["push", "-u", "origin", "main"], "bind_all_done")
            return

        if tag == "bind_all_done":
            self._log(out or "已成功綁定並推送至 GitHub 雲端！")
            self.refresh_local_all()
            self.cloud_repo_created.emit()
            if success:
                QMessageBox.information(self, "上雲成功 🎉", "本機專案已成功建立 GitHub 雲端倉庫並完成首次同步！")
            else:
                QMessageBox.warning(self, "推送異常", f"雲端倉庫已建立，但推送時發生錯誤：\n{out}")
            return

        if tag.startswith("clone|"):
            target_path = tag.split("|", 1)[1]
            self._log(out or "Clone 完成")
            if success and os.path.isdir(target_path):
                self.current_repo = target_path
                self.repo_path_input.setText(target_path)
                self.config["last_repo"] = target_path
                self.save_config()
                self.refresh_local_all()
            return

        if tag == "remote_url":
            if success:
                m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", out.strip())
                if m:
                    self._fetch_cloud_detail(m.group(1))
            else:
                self.cloud_detail_viewer.setPlainText(
                    "【提示】左側本機資料夾尚未綁定任何 GitHub 雲端倉庫。\n"
                    "👉 你可以直接點擊上方紫色的「☁️ 新建並綁定雲端倉庫」按鈕，程式會全自動幫你在 GitHub 建好倉庫並上傳！"
                )
            return

        if tag == "status":
            self.changes_list.clear()
            for line in out.splitlines():
                if line.strip():
                    self.changes_list.addItem(line)
            if self.changes_list.count() == 0:
                self.changes_list.addItem("(工作區乾淨，無未提交變更)")

        elif tag == "branches":
            self.branch_combo.clear()
            current_idx = 0
            for idx, line in enumerate(out.splitlines()):
                b_name = line.strip()
                if b_name.startswith("* "):
                    b_name = b_name[2:]
                    current_idx = idx
                self.branch_combo.addItem(b_name)
            self.branch_combo.setCurrentIndex(current_idx)

        elif tag == "log":
            self.history_list.clear()
            for line in out.splitlines():
                if line.strip():
                    self.history_list.addItem(line)

        elif tag == "diff":
            self.diff_viewer.setPlainText(out if out.strip() else "此檔案無文字差異 (可能為未追蹤新檔或二進位檔)")

        elif tag == "add_done":
            msg = self.commit_msg_input.text().strip() or "Update via GitHub Manager"
            self._run_git(["commit", "-m", msg], "commit_done")

        elif tag == "commit_done":
            self._log(out)
            self.commit_msg_input.clear()
            chk_remote = subprocess.run(
                ["git", "remote", "get-url", "origin"], cwd=self.current_repo, capture_output=True, text=True
            )
            if chk_remote.returncode != 0:
                self.refresh_local_all()
                ans = QMessageBox.question(
                    self, "尚未綁定雲端倉庫",
                    "本機版本紀錄已建立成功！\n偵測到此專案尚未綁定 GitHub 雲端倉庫，是否現在自動在 GitHub 建立雲端倉庫並推送上去？"
                )
                if ans == QMessageBox.Yes:
                    self._open_create_cloud_repo_dialog()
                return

            cur_br = self.branch_combo.currentText() or "main"
            self._run_git(["push", "-u", "origin", cur_br], "push_done")

        elif tag in ("push_done", "pull", "switch", "merge", "reset", "init"):
            self._log(f"[{tag}] {out if out.strip() else '執行成功'}")
            self.refresh_local_all()

    def _on_file_clicked(self, item):
        text = item.text()
        if text.startswith("("):
            return
        file_path = text[3:].strip().strip('"')
        self._run_git(["diff", "HEAD", "--", file_path], "diff")

    def _on_commit_clicked(self, item):
        commit_hash = item.text().split()[0]
        self._run_git(["show", commit_hash], "diff")

    def _one_click_commit_push(self):
        if not self.current_repo:
            QMessageBox.warning(self, "提示", "請先在左側點擊「開啟」選擇本機專案資料夾！")
            return
        ensure_safe_gitignore(self.current_repo)
        self._log("執行一鍵提交並推送至雲端 (add -> commit -> push)...")
        self._run_git(["add", "."], "add_done")

    def _switch_branch(self):
        br = self.branch_combo.currentText()
        if br:
            self._run_git(["checkout", br], "switch")

    def _create_branch(self):
        name, ok = QInputDialog.getText(self, "建新版本", "請輸入新版本名稱 (建議英文/數字，如 v2-test):")
        if ok and name.strip():
            self._run_git(["checkout", "-b", name.strip()], "switch")

    def _merge_branch(self):
        br = self.branch_combo.currentText()
        if br:
            self._run_git(["merge", br], "merge")

    def _revert_selected_commit(self):
        item = self.history_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "請先在歷史清單中點選要還原的版本！")
            return
        commit_hash = item.text().split()[0]
        ans = QMessageBox.warning(
            self, "危險操作確認",
            f"確定要將本機專案強制還原至 {commit_hash} 嗎？\n未提交的變更將會遺失！",
            QMessageBox.Yes | QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            self._run_git(["reset", "--hard", commit_hash], "reset")


# ==========================================
# 8. 分頁二 & 分頁三：網址分析器 / 全站關鍵字搜尋
# ==========================================
class GithubExploreTab(QWidget):
    def __init__(self, mode, get_token_func, clone_callback, analyze_url_callback=None):
        super().__init__()
        self.mode = mode
        self.get_token = get_token_func
        self.clone_callback = clone_callback
        self.analyze_url_callback = analyze_url_callback
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        self.input_box = QLineEdit()
        if self.mode == "url":
            self.input_box.setPlaceholderText("輸入 GitHub 帳號首頁 (如 https://github.com/d93432327) 或任何包含專案連結的網址...")
            btn_text = "分析頁面儲存庫"
            btn_tip = "【用途】自動解析輸入網址中的所有 GitHub 儲存庫並列出清單。\n【使用時機】貼上某位作者首頁、專案網址或技術文章連結，想批次抓取裡面專案時使用。"
        else:
            self.input_box.setPlaceholderText("輸入搜尋關鍵字 (支援語法如: PySide6 overlay language:python stars:>20)...")
            btn_text = "搜尋 GitHub"
            btn_tip = "【用途】依照關鍵字搜尋全站最熱門的 GitHub 專案。\n【使用時機】尋找開源工具或參考程式碼時使用（搜尋結果雙擊可轉跳分頁 2 深入分析）。"

        self.btn_go = QPushButton(btn_text)
        self.btn_go.setStyleSheet("background-color: #238636; color: white; font-weight: bold; padding: 5px 14px;")
        self.btn_go.setToolTip(btn_tip)
        self.btn_go.clicked.connect(self._execute)
        self.input_box.returnPressed.connect(self._execute)

        top_bar.addWidget(self.input_box)
        top_bar.addWidget(self.btn_go)
        layout.addLayout(top_bar)

        self.batch_panel = RepoBatchPanel(
            self.get_token,
            enable_double_click_analyze=(self.mode == "search")
        )
        self.batch_panel.clone_requested.connect(self.clone_callback)
        if self.analyze_url_callback:
            self.batch_panel.analyze_requested.connect(self.analyze_url_callback)

        layout.addWidget(self.batch_panel)

    def load_and_analyze_url(self, url):
        self.input_box.setText(url)
        self._execute()

    def _execute(self):
        q = self.input_box.text().strip()
        if not q:
            return
        self.btn_go.setEnabled(False)
        self.batch_panel.status_lbl.setText("正在連線 GitHub 取得資料...")
        self.worker = ApiSearchWorker(self.mode, q, self.get_token())
        self.worker.result_ready.connect(self._on_results)
        self.worker.start()

    def _on_results(self, repos, err):
        self.btn_go.setEnabled(True)
        if err:
            self.batch_panel.status_lbl.setText("查詢失敗")
            QMessageBox.warning(self, "錯誤", err)
            return
        self.batch_panel.populate(repos)
        hint = "（💡 雙擊任一列可轉跳至「2. 網址頁面分析」深入解析）" if self.mode == "search" else ""
        self.batch_panel.status_lbl.setText(f"共找到 {len(repos)} 個儲存庫 {hint}")


# ==========================================
# 9. 主視窗整合框架 (含四大分類遊戲式新手引導選單)
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitHub 全功能工作台 (雙欄本地/雲端同步 / 頁面解析 / 全站搜尋下載)")
        self.resize(1240, 800)
        self.config = self._load_config()
        self.current_token = self.config.get("github_token", "")
        self.oauth_dialog = None
        self.oauth_worker = None
        self.tutorial_overlay = None
        self._init_ui()

        if self.current_token:
            self._verify_and_sync_token(self.current_token, silent=True)
        else:
            sys_token = get_system_git_credential()
            if sys_token:
                self._verify_and_sync_token(sys_token, silent=True)

        # 首次啟動自動開啟「1. 全新專案上傳教學」
        if not self.config.get("tutorial_completed", False):
            QTimer.singleShot(500, lambda: self.launch_tutorial_topic("upload"))

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 頂部 GitHub 帳號登入 / 登出控制列
        auth_bar = QHBoxLayout()
        self.account_badge = QLabel("● 未登入")
        self.account_badge.setStyleSheet("color: #f85149; font-weight: bold; padding-right: 10px;")

        self.btn_browser_login = QPushButton("🌐 瀏覽器網頁一鍵登入 (免手動貼 Token)")
        self.btn_browser_login.setStyleSheet("background-color: #238636; color: white; font-weight: bold; padding: 5px 14px;")
        self.btn_browser_login.setToolTip("【用途】自動開啟瀏覽器並複製驗證碼，授權後即可免密碼登入 GitHub。\n【使用時機】首次使用或切換帳號時使用，登入後可存取私有倉庫並同步右側雲端列表。")
        self.btn_browser_login.clicked.connect(self._start_browser_login)

        self.btn_token_login = QPushButton("改用 Token 登入")
        self.btn_token_login.setToolTip("【用途】手動貼上 ghp_ 開頭的個人存取權杖進行登入。\n【使用時機】無瀏覽器環境或已備妥專用 Token 時使用。")
        self.btn_token_login.clicked.connect(self._prompt_token_login)

        self.btn_refresh_cloud = QPushButton("🔄 重新整理雲端")
        self.btn_refresh_cloud.setVisible(False)
        self.btn_refresh_cloud.setToolTip("【用途】重新從 GitHub 抓取你帳號底下的最新雲端倉庫清單。\n【使用時機】剛在網頁版新建倉庫或修改權限後使用。")
        self.btn_refresh_cloud.clicked.connect(lambda: self._verify_and_sync_token(self.current_token, silent=False))

        # 分類式新手引導選單按鈕
        self.btn_tutorial = QPushButton("🎮 新手引導教學 ▾")
        self.btn_tutorial.setStyleSheet("background-color: #d29922; color: #161b22; font-weight: bold; padding: 5px 12px;")
        self.btn_tutorial.setToolTip("點擊選擇你想學習的功能教學關卡（含遊戲式紅箭頭與聚光燈引導）")

        tut_menu = QMenu(self)
        tut_menu.setStyleSheet("QMenu { font-size: 10pt; padding: 4px; } QMenu::item { padding: 6px 18px; }")
        act1 = QAction("1. 🚀 全新專案上傳教學 (登入 / 開資料夾 / 建雲端庫上雲)", self)
        act2 = QAction("2. 🌿 版本分支管理教學 (建新版本 / 切換 / 合併回主版)", self)
        act3 = QAction("3. 🔄 雲端下載、檔案比對與還原教學 (Pull / Diff / 時光機)", self)
        act4 = QAction("4. 🔍 資源搜尋與批次下載教學 (搜尋 / 雙擊解析 / 打包解壓)", self)

        act1.triggered.connect(lambda: self.launch_tutorial_topic("upload"))
        act2.triggered.connect(lambda: self.launch_tutorial_topic("branch"))
        act3.triggered.connect(lambda: self.launch_tutorial_topic("sync_diff"))
        act4.triggered.connect(lambda: self.launch_tutorial_topic("explore"))

        tut_menu.addAction(act1)
        tut_menu.addAction(act2)
        tut_menu.addAction(act3)
        tut_menu.addAction(act4)
        self.btn_tutorial.setMenu(tut_menu)

        self.btn_logout = QPushButton("登出帳號")
        self.btn_logout.setStyleSheet("background-color: #da3633; color: white; padding: 5px 12px;")
        self.btn_logout.setVisible(False)
        self.btn_logout.setToolTip("【用途】登出目前 GitHub 帳號並清除本機儲存的登入憑證。\n【使用時機】共用電腦離開前，或準備切換另一組 GitHub 帳號時使用。")
        self.btn_logout.clicked.connect(self._logout_account)

        auth_bar.addWidget(self.account_badge)
        auth_bar.addWidget(self.btn_browser_login)
        auth_bar.addWidget(self.btn_token_login)
        auth_bar.addWidget(self.btn_refresh_cloud)
        auth_bar.addStretch()
        auth_bar.addWidget(self.btn_tutorial)
        auth_bar.addWidget(self.btn_logout)
        main_layout.addLayout(auth_bar)

        # 三大核心分頁
        self.tabs = QTabWidget()
        self.tab_local = DualPaneGitTab(self.config, self._save_config, self.get_token)
        self.tab_local.cloud_repo_created.connect(
            lambda: self._verify_and_sync_token(self.current_token, silent=True)
        )
        self.tab_url = GithubExploreTab("url", self.get_token, self._jump_and_clone)
        self.tab_search = GithubExploreTab("search", self.get_token, self._jump_and_clone, self._jump_and_analyze_url)

        self.tabs.addTab(self.tab_local, "1. 雙欄倉庫管理 (左：本機 / 右：GitHub 雲端)")
        self.tabs.addTab(self.tab_url, "2. 網址頁面分析與批次下載")
        self.tabs.addTab(self.tab_search, "3. GitHub 全站關鍵字搜尋")
        main_layout.addWidget(self.tabs)

    # --- 四大分類遊戲式新手引導腳本 ---
    def launch_tutorial_topic(self, topic_key):
        if self.tutorial_overlay:
            self.tutorial_overlay.deleteLater()
            self.tutorial_overlay = None

        login_btn_fn = lambda: self.btn_browser_login if self.btn_browser_login.isVisible() else self.btn_refresh_cloud

        catalog = {
            "upload": (
                "1. 全新專案上傳教學",
                [
                    {
                        "title": "第一步：連接你的 GitHub 帳號",
                        "desc": "先點擊紅箭頭指著的「瀏覽器網頁一鍵登入」，在瀏覽器確認授權後，右側就會自動載入你的雲端帳號資訊！（若已顯示綠燈已登入，請直接按「下一步」）",
                        "widget": login_btn_fn,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第二步：開啟本機新專案資料夾",
                        "desc": "點擊左側的「開啟」按鈕，挑選你電腦裡的資料夾。就算是全新未建立版控的資料夾，程式也會自動幫你初始化並建立安全防護檔（排除敏感設定）！",
                        "widget": self.tab_local.btn_open,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第三步：填寫首次提交說明（支援多行）",
                        "desc": "在下方輸入框打上這次的版本說明（例如：Initial commit 建立專案）。按 Enter 可以自由換行，滑鼠移開會自動縮回省空間，移入或點擊就能再次展開修改！",
                        "widget": self.tab_local.commit_msg_input,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第四步：一鍵建立雲端倉庫並推送上雲",
                        "desc": "點擊右側紫色的「☁️ 新建並綁定雲端倉庫」，輸入英文倉庫名稱並選擇公開或私有，程式就會全自動在 GitHub 建好倉庫並把本機檔案推上去！",
                        "widget": self.tab_local.btn_create_cloud,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    }
                ]
            ),
            "branch": (
                "2. 版本分支管理教學",
                [
                    {
                        "title": "第一步：從目前進度「建新版本」",
                        "desc": "想測試新功能或大幅改寫程式、又怕改壞原本穩定運作的主版本（main）時，點擊「建新版本」輸入名稱（例如 v2-test），就會複製出一條獨立版本讓你安心修改！",
                        "widget": self.tab_local.btn_new_br,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第二步：在不同版本之間「切換版本」",
                        "desc": "開發過程中，隨時可以在左側下拉選單挑選主版本（main）或實驗版本，再按「切換版本」，本機資料夾裡的檔案就會瞬間切換成該版本的狀態！",
                        "widget": self.tab_local.btn_sw,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第三步：測試完成後「合併版本」",
                        "desc": "當新版本的功能測試完全正常，先切回主版本（main），再於下拉選單選取實驗版本並按下「合併版本」，就能把新功能無痛整合進正式主線！",
                        "widget": self.tab_local.btn_merge,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第四步：將合併後的最新版推上雲端",
                        "desc": "合併完成後，按下藍色的「↑ 一鍵提交並 Push」，就能把最新的正式版本同步備份到 GitHub 雲端！",
                        "widget": self.tab_local.btn_sync,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    }
                ]
            ),
            "sync_diff": (
                "3. 雲端下載、檔案比對與還原教學",
                [
                    {
                        "title": "第一步：開工前先從雲端「↓ Pull」或 Clone",
                        "desc": "如果在別台電腦改過程式並上傳，這台電腦開工前先按「↓ Pull」拉取最新進度；若是第一次下載雲端專案，則點右側「↙ Clone 選取倉庫至本機」。",
                        "widget": self.tab_local.btn_pull,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第二步：點擊變更檔案檢視紅綠 Diff 對比",
                        "desc": "修改存檔後，點擊左側「變更檔案」清單中的任一檔名，下方黑窗就會用「綠底顯示新增、紅底顯示刪除」，讓你清楚檢查每一行程式碼的改動！",
                        "widget": self.tab_local.changes_list,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第三步：寫好說明並「↑ 一鍵提交並 Push」",
                        "desc": "確認紅綠差異都沒問題後，在下方輸入框寫一句修改紀錄（按 Enter 可換行），再按這顆藍色按鈕即可一鍵存檔並上傳雲端。",
                        "widget": self.tab_local.btn_sync,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    },
                    {
                        "title": "第四步：改壞想重來？使用「還原此版」時光機",
                        "desc": "萬一程式改亂想放棄重來，先在「本機提交歷史」清單點選過去正常的版本，再按紅色的「還原此版」，所有檔案就會立刻退回該時間點！",
                        "widget": self.tab_local.btn_revert,
                        "before_show": lambda: self.tabs.setCurrentIndex(0)
                    }
                ]
            ),
            "explore": (
                "4. 資源搜尋與批次下載教學",
                [
                    {
                        "title": "第一步：輸入關鍵字搜尋全站專案",
                        "desc": "切換到第 3 分頁後，在搜尋框輸入關鍵字（支援進階語法如 PySide6 language:python stars:>20），按下「搜尋 GitHub」即可列出全站高星數專案！",
                        "widget": self.tab_search.btn_go,
                        "before_show": lambda: self.tabs.setCurrentIndex(2)
                    },
                    {
                        "title": "第二步：在表格「按兩下」轉跳深度分析",
                        "desc": "看到感興趣的專案時，直接在搜尋結果表格的該列「按滑鼠左鍵兩下」，程式會自動跳轉到第 2 分頁並解析該頁面裡的所有相關專案連結！",
                        "widget": self.tab_search.batch_panel.table,
                        "before_show": lambda: self.tabs.setCurrentIndex(2)
                    },
                    {
                        "title": "第三步：貼上任何網址分析裡面的儲存庫",
                        "desc": "你也可以直接來到第 2 分頁，貼上任何 GitHub 作者首頁或技術文章網址，按「分析頁面儲存庫」就能把頁面內所有專案一次抓出來！",
                        "widget": self.tab_url.btn_go,
                        "before_show": lambda: self.tabs.setCurrentIndex(1)
                    },
                    {
                        "title": "第四步：勾選並批次打包下載、自動解壓縮",
                        "desc": "勾選想要抓回來的專案（可按全選），確認存放路徑與「自動解壓縮並刪除 ZIP」有勾選後，按這顆綠色按鈕就能一次全部下載解壓縮到電腦裡！",
                        "widget": self.tab_url.batch_panel.btn_download,
                        "before_show": lambda: self.tabs.setCurrentIndex(1)
                    }
                ]
            )
        }

        topic_title, steps = catalog[topic_key]
        self.tutorial_overlay = GameTutorialOverlay(self, topic_title, steps)
        self.tutorial_overlay.show()

    def on_tutorial_finished(self):
        self.tutorial_overlay = None
        self.config["tutorial_completed"] = True
        self._save_config()

    def get_token(self):
        return self.current_token

    def _start_browser_login(self):
        self.btn_browser_login.setEnabled(False)
        self.account_badge.setText("● 正在啟動瀏覽器授權...")
        self.account_badge.setStyleSheet("color: #d29922; font-weight: bold;")

        self.oauth_worker = DeviceOAuthWorker(GITHUB_CLIENT_ID)
        self.oauth_worker.code_ready.connect(self._on_device_code_ready)
        self.oauth_worker.auth_finished.connect(self._on_browser_oauth_done)
        self.oauth_worker.start()

    def _on_device_code_ready(self, user_code, verify_uri):
        QApplication.clipboard().setText(user_code)
        webbrowser.open(verify_uri)

        self.oauth_dialog = BrowserAuthDialog(user_code, verify_uri, self)
        self.oauth_dialog.rejected.connect(self._cancel_browser_login)
        self.oauth_dialog.show()

    def _cancel_browser_login(self):
        if self.oauth_worker and self.oauth_worker.isRunning():
            self.oauth_worker.cancel()
        self.btn_browser_login.setEnabled(True)
        if not self.current_token:
            self.account_badge.setText("● 未登入")
            self.account_badge.setStyleSheet("color: #f85149; font-weight: bold;")

    def _on_browser_oauth_done(self, success, result):
        if self.oauth_dialog:
            self.oauth_dialog.close()
            self.oauth_dialog = None
        self.btn_browser_login.setEnabled(True)

        if not success:
            self.account_badge.setText("● 登入失敗")
            self.account_badge.setStyleSheet("color: #f85149; font-weight: bold;")
            QMessageBox.warning(self, "瀏覽器授權未完成", result)
            return

        self._verify_and_sync_token(result, silent=False)

    def _prompt_token_login(self):
        token, ok = QInputDialog.getText(self, "Token 登入", "請貼上 GitHub Personal Access Token (ghp_xxxx):", QLineEdit.Password)
        if ok and token.strip():
            self._verify_and_sync_token(token.strip(), silent=False)

    def _verify_and_sync_token(self, token, silent=False):
        self.account_badge.setText("● 正在同步帳號資料...")
        self.account_badge.setStyleSheet("color: #d29922; font-weight: bold;")

        self.auth_worker = AuthAndCloudWorker(token)
        self.auth_worker.auth_result.connect(lambda ok, u, repos, err: self._on_auth_done(ok, u, repos, err, token, silent))
        self.auth_worker.start()

    def _on_auth_done(self, ok, user_info, repos, err, token, silent):
        if not ok:
            self.account_badge.setText("● 登入失敗")
            self.account_badge.setStyleSheet("color: #f85149; font-weight: bold;")
            if not silent:
                QMessageBox.critical(self, "GitHub 登入失敗", err)
            return

        self.current_token = token
        self.config["github_token"] = token
        self._save_config()

        login_name = user_info.get("login", "User")
        self.account_badge.setText(f"● 已登入: {login_name}")
        self.account_badge.setStyleSheet("color: #3fb950; font-weight: bold; padding-right: 10px;")

        self.btn_browser_login.setVisible(False)
        self.btn_token_login.setVisible(False)
        self.btn_refresh_cloud.setVisible(True)
        self.btn_logout.setVisible(True)

        self.tab_local.update_cloud_account(True, user_info, repos)

    def _logout_account(self):
        ans = QMessageBox.question(self, "登出確認", "確定要登出目前 GitHub 帳號嗎？")
        if ans != QMessageBox.Yes:
            return

        self.current_token = ""
        self.config.pop("github_token", None)
        self._save_config()

        self.account_badge.setText("● 未登入")
        self.account_badge.setStyleSheet("color: #f85149; font-weight: bold; padding-right: 10px;")

        self.btn_browser_login.setVisible(True)
        self.btn_token_login.setVisible(True)
        self.btn_refresh_cloud.setVisible(False)
        self.btn_logout.setVisible(False)

        self.tab_local.update_cloud_account(False)

    def _jump_and_clone(self, git_url):
        self.tabs.setCurrentIndex(0)
        self.tab_local.prompt_clone(git_url)

    def _jump_and_analyze_url(self, repo_html_url):
        self.tabs.setCurrentIndex(1)
        self.tab_url.load_and_analyze_url(repo_html_url)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
