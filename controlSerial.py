import sys
import json
import serial
import serial.tools.list_ports
import socket
import os
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QComboBox,
    QLabel, QTextEdit, QHBoxLayout, QLineEdit, QStackedLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPointF, QTimer
from PyQt6.QtGui import QIcon, QPainter, QPen, QBrush, QColor, QMouseEvent, QTextCursor

CONFIG_FILE = "config.json"


class SerialReader(QThread):
    data_received = pyqtSignal(str)

    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = True

    def run(self):
        while self.running:
            if self.serial_port and self.serial_port.is_open:
                try:
                    line = self.serial_port.readline().decode('utf-8').strip()
                    if line:
                        self.data_received.emit(line)
                except Exception as e:
                    self.data_received.emit(f"Ошибка чтения: {e}")
                    self.running = False

    def stop(self):
        self.running = False


class UDPListener(threading.Thread):
    def __init__(self, ip, port, callback):
        super().__init__(daemon=True)
        self.ip = ip
        self.port = port
        self.callback = callback
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = True

    def run(self):
        try:
            self.sock.bind((self.ip, self.port))
            while self.running:
                try:
                    data, _ = self.sock.recvfrom(1024)
                    if data:
                        self.callback(data.decode('utf-8').strip())
                except OSError as e:
                    if not self.running:
                        break  # сокет закрыт вручную
                    self.callback(f"UDP Error: {e}")
        except Exception as e:
            self.callback(f"UDP Error: {e}")

    def stop(self):
        self.running = False
        self.sock.close()


class JoystickWidget(QWidget):
    moved = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.radius = 80
        self.center = QPointF(100, 100)
        self.knob_pos = self.center
        self.active = False
        self.target_speedL = 0
        self.target_speedR = 0
        self.current_speedL = 0
        self.current_speedR = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_speeds)
        self.timer.start(30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(230, 230, 230)))
        painter.setPen(QPen(Qt.GlobalColor.black, 2))
        painter.drawEllipse(self.center, self.radius, self.radius)
        dx = self.knob_pos.x() - self.center.x()
        dy = self.knob_pos.y() - self.center.y()
        if dx != 0 or dy != 0:
            painter.setPen(QPen(QColor(100, 100, 255), 3))
            painter.drawLine(self.center, self.knob_pos)
        painter.setBrush(QBrush(QColor(100, 100, 255)))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(self.knob_pos, 15, 15)

    def mousePressEvent(self, event: QMouseEvent):
        self.active = True
        self.update_knob(event.pos())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.active:
            self.update_knob(event.pos())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.active = False
        self.knob_pos = self.center
        self.update()
        self.target_speedL = 0
        self.target_speedR = 0

    def update_knob(self, pos: QPointF):
        dx = pos.x() - self.center.x()
        dy = pos.y() - self.center.y()
        dist = (dx**2 + dy**2)**0.5
        if dist > self.radius:
            scale = self.radius / dist
            dx *= scale
            dy *= scale
        self.knob_pos = QPointF(self.center.x() + dx, self.center.y() + dy)
        self.update()
        norm_x = dx / self.radius
        norm_y = -dy / self.radius
        speedL = int((norm_y - norm_x) * 255)
        speedR = int((norm_y + norm_x) * 255)
        self.target_speedL = max(-255, min(255, speedL))
        self.target_speedR = max(-255, min(255, speedR))

    def update_speeds(self):
        def approach(current, target, step=15):
            if current < target:
                return min(current + step, target)
            elif current > target:
                return max(current - step, target)
            return current
        self.current_speedL = approach(self.current_speedL, self.target_speedL)
        self.current_speedR = approach(self.current_speedR, self.target_speedR)
        self.moved.emit(self.current_speedL, self.current_speedR)


class SerialController(QWidget):
    
    def __init__(self):
        super().__init__()
        self.current_data = {"speedL": 0, "speedR": 0, "brake": False, "limit": 1.0}
        self.serial_port = None
        self.reader_thread = None
        self.use_udp = False
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_ip = "127.0.0.1"
        self.udp_port = 5005
        self.udp_listener = None
        self.initUI()
        self.load_settings()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def initUI(self):
        layout = QVBoxLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["COM", "UDP"])
        self.mode_combo.currentTextChanged.connect(self.toggle_mode)
        layout.addWidget(QLabel("Режим отправки:"))
        layout.addWidget(self.mode_combo)

        self.stack = QStackedLayout()
        com_layout = QVBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton()
        self.refresh_button.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_button.setFixedSize(24, 24)
        self.refresh_button.clicked.connect(self.refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port_combo)
        port_row.addWidget(self.refresh_button)
        com_layout.addWidget(QLabel("COM порт:"))
        com_layout.addLayout(port_row)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "115200", "250000"])
        com_layout.addWidget(QLabel("Скорость:"))
        com_layout.addWidget(self.baud_combo)
        self.connect_button = QPushButton("Подключиться")
        self.connect_button.clicked.connect(self.connect_serial)
        com_layout.addWidget(self.connect_button)
        com_widget = QWidget()
        com_widget.setLayout(com_layout)
        self.stack.addWidget(com_widget)

        udp_layout = QVBoxLayout()
        self.ip_input = QLineEdit("127.0.0.1")
        self.port_input = QLineEdit("5005")
        self.port_output = QLineEdit("5006")
        udp_layout.addWidget(QLabel("UDP IP:"))
        udp_layout.addWidget(self.ip_input)
        udp_layout.addWidget(QLabel("UDP Port:"))
        udp_layout.addWidget(self.port_input)
        udp_layout.addWidget(QLabel("UDP Port RX:"))
        udp_layout.addWidget(self.port_output)
        self.restart_udp_button = QPushButton("Перезапустить UDP")
        self.restart_udp_button.clicked.connect(self.start_udp_listener)
        udp_layout.addWidget(self.restart_udp_button)
        udp_widget = QWidget()
        udp_widget.setLayout(udp_layout)
        self.stack.addWidget(udp_widget)

        layout.addLayout(self.stack)

        self.brake_button = QPushButton("Тормоз")
        self.brake_button.pressed.connect(self.press_brake)
        self.brake_button.released.connect(self.release_brake)
        layout.addWidget(self.brake_button)

        self.speedL_label = QLabel("L: 0")
        self.speedR_label = QLabel("R: 0")
        layout.addWidget(self.speedL_label)
        layout.addWidget(self.speedR_label)

        self.joystick = JoystickWidget()
        self.joystick.moved.connect(self.joystick_move)
        layout.addWidget(QLabel("Джойстик:"))
        layout.addWidget(self.joystick)

        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        layout.addWidget(self.terminal_output)

        self.setLayout(layout)
        self.setWindowTitle("Управление Мотор-колесом COM/UDP")
        self.setMinimumSize(400, 600)

    def toggle_mode(self, mode):
        self.use_udp = (mode == "UDP")
        self.stack.setCurrentIndex(1 if self.use_udp else 0)
        if self.use_udp:
            self.start_udp_listener()

    def refresh_ports(self):
        self.port_combo.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo.addItems(ports)

    def connect_serial(self):
        if self.serial_port and self.serial_port.is_open:
            if self.reader_thread:
                self.reader_thread.stop()
            self.serial_port.close()
            self.serial_port = None
            self.connect_button.setText("Подключиться")
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.terminal_output.append("Отключено")
            return

        port = self.port_combo.currentText()
        baud = int(self.baud_combo.currentText())
        try:
            self.serial_port = serial.Serial(port, baud, timeout=1)
            self.connect_button.setText("Отключиться")
            self.port_combo.setEnabled(False)
            self.baud_combo.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.terminal_output.append(f"Подключено к {port} @ {baud}")
            self.save_settings(port, baud, self.ip_input.text(), self.port_input.text(), self.port_output.text())
            self.reader_thread = SerialReader(self.serial_port)
            self.reader_thread.data_received.connect(self.terminal_output.append)
            self.reader_thread.start()
        except serial.SerialException as e:
            self.terminal_output.append(f"Ошибка подключения: {e}")
            self.serial_port = None

    def start_udp_listener(self):
        if self.udp_listener:
            self.udp_listener.stop()
            self.udp_listener.join()
            self.udp_listener = None
        ip = self.ip_input.text()
        try:
            port = int(self.port_output.text())
        except ValueError:
            self.terminal_output.append("Ошибка: неверный порт UDP RX")
            return
        self.udp_listener = UDPListener(ip, port, self.filter_incoming)
        self.udp_listener.start()
        self.terminal_output.append(f"UDP RX слушает {ip}:{port}")
    
    def save_settings(self, port, baud, udp_ip, udp_port, udp_port_out):
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "port": port,
                "baudrate": baud,
                "udp_ip": udp_ip,
                "udp_port": udp_port,
                "udp_port_out": udp_port_out
            }, f)

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            ports = [p.device for p in serial.tools.list_ports.comports()]
            if cfg.get("port") in ports:
                self.port_combo.setCurrentText(cfg["port"])
            if "baudrate" in cfg:
                self.baud_combo.setCurrentText(str(cfg["baudrate"]))
            if "udp_ip" in cfg:
                self.ip_input.setText(cfg["udp_ip"])
            if "udp_port" in cfg:
                self.port_input.setText(str(cfg["udp_port"]))
            if "udp_port_out" in cfg:
                self.port_output.setText(str(cfg["udp_port_out"]))

    
    def send_data(self):
        valL = int(self.current_data["speedL"] * self.current_data["limit"])
        valR = int(self.current_data["speedR"] * self.current_data["limit"])
        b = 1 if self.current_data["brake"] else 0
        packet_data = f"{valL},{valR},{b}"
        packet = f"TX:{packet_data}\n"

        if hasattr(self, "last_packet") and self.last_packet == packet:
            return
        self.last_packet = packet

        if self.use_udp:
            self.udp_ip = self.ip_input.text()
            self.udp_port = int(self.port_input.text())
            try:
                self.udp_socket.sendto(packet.encode('utf-8'), (self.udp_ip, self.udp_port))
                self.terminal_output.append(f"Отправка: {packet_data}")
                self.terminal_output.moveCursor(QTextCursor.MoveOperation.End)
            except Exception as e:
                self.terminal_output.append(f"Ошибка UDP: {e}")
        else:
            if self.serial_port and self.serial_port.is_open:
                try:
                    self.serial_port.write(packet.encode('utf-8'))
                    self.terminal_output.append(f"Отправка: {packet_data}")
                    self.terminal_output.moveCursor(QTextCursor.MoveOperation.End)
                except Exception as e:
                    self.terminal_output.append(f"Ошибка COM: {e}")

    def filter_incoming(self, line):
        if line.startswith("RX:"):
            self.terminal_output.append(f"{line}")
            self.terminal_output.moveCursor(QTextCursor.MoveOperation.End)
        
    
    def connect_serial(self):
        if self.serial_port and self.serial_port.is_open:
            if self.reader_thread:
                self.reader_thread.stop()
            self.serial_port.close()
            self.serial_port = None
            self.connect_button.setText("Подключиться")
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.terminal_output.append("Отключено")
            return
    
        port = self.port_combo.currentText()
        baud = int(self.baud_combo.currentText())
        try:
            self.serial_port = serial.Serial(port, baud, timeout=1)
            self.connect_button.setText("Отключиться")
            self.port_combo.setEnabled(False)
            self.baud_combo.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.terminal_output.append(f"Подключено к {port} @ {baud}")
            self.save_settings(port, baud, self.ip_input.text(), self.port_input.text())
            self.reader_thread = SerialReader(self.serial_port)
            self.reader_thread.data_received.connect(self.filter_incoming)
            self.reader_thread.start()
        except serial.SerialException as e:
            self.terminal_output.append(f"Ошибка подключения: {e}")
            self.serial_port = None

    def joystick_move(self, speedL, speedR):
        self.current_data["speedL"] = speedL
        self.current_data["speedR"] = speedR
        self.speedL_label.setText(f"L: {speedL}")
        self.speedR_label.setText(f"R: {speedR}")
        self.send_data()

    def press_brake(self):
        self.current_data["brake"] = True
        self.brake_button.setStyleSheet("background-color: red;")
        self.send_data()

    def release_brake(self):
        self.current_data["brake"] = False
        self.brake_button.setStyleSheet("")
        self.send_data()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not self.current_data["brake"]:
            self.press_brake()

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.current_data["brake"]:
            self.release_brake()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SerialController()
    window.show()
    sys.exit(app.exec())
