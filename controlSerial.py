import sys
import json
import serial
import serial.tools.list_ports
import os
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QComboBox,
    QLabel, QSlider, QHBoxLayout, QTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon

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

class SerialController(QWidget):
    def __init__(self):
        super().__init__()
        self.current_data = {
            "speed": 0,
            "brake": False,
            "limit": 1.0
        }
        self.serial_port = None
        self.reader_thread = None
        self.initUI()
        self.load_settings()

    def initUI(self):
        layout = QVBoxLayout()
        
        # --- Port selection ---
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton()
        self.refresh_button.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_button.setFixedSize(24, 24)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.port_combo_layout = QHBoxLayout()
        self.port_combo_layout.addWidget(self.port_combo)
        self.port_combo_layout.addWidget(self.refresh_button)
        self.refresh_ports()
        layout.addWidget(QLabel("Выберите COM порт:"))
        layout.addLayout(self.port_combo_layout)
        
        # --- Baudrate ---
        layout.addWidget(QLabel("Скорость порта:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "115200", "250000"])
        layout.addWidget(self.baud_combo)
        
        # --- Connect button ---
        self.connect_button = QPushButton("Подключиться")
        self.connect_button.clicked.connect(self.connect_serial)
        layout.addWidget(self.connect_button)
        
        # --- Brake button ---
        self.brake_button = QPushButton("Тормоз")
        self.brake_button.pressed.connect(self.press_brake)
        self.brake_button.released.connect(self.release_brake)
        layout.addWidget(self.brake_button)
        
        # --- Slider for speed and power limit ---
        sliders_layout = QHBoxLayout()
        
        # Speed slider
        self.speed_label = QLabel("Скорость: 0")
        self.speed_slider = QSlider(Qt.Orientation.Vertical)
        self.speed_slider.setRange(-255, 255)
        self.speed_slider.setSingleStep(1)
        self.speed_slider.setPageStep(10)
        self.speed_slider.setValue(0)
        self.speed_slider.valueChanged.connect(self.update_speed)
        self.speed_slider.sliderReleased.connect(self.reset_speed)
        v1 = QVBoxLayout(); v1.addWidget(self.speed_label); v1.addWidget(self.speed_slider)
        sliders_layout.addLayout(v1)

        # Power limit slider
        self.limit_label = QLabel("Мощность: 100%")
        self.limit_slider = QSlider(Qt.Orientation.Vertical)
        self.limit_slider.setMinimum(1)
        self.limit_slider.setMaximum(5)
        self.limit_slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
        self.limit_slider.setTickInterval(1)
        self.limit_slider.setValue(5)
        self.limit_slider.valueChanged.connect(self.update_limit)
        v2 = QVBoxLayout(); v2.addWidget(self.limit_label); v2.addWidget(self.limit_slider)
        sliders_layout.addLayout(v2)
        
        layout.addLayout(sliders_layout)
        
        # --- Terminal output ---
        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        layout.addWidget(self.terminal_output)
        
        self.setLayout(layout)
        self.setWindowTitle("Управление Мотор-колесом COM")
        self.setMinimumSize(400, 500)

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
            self.save_settings(port, baud)
            self.reader_thread = SerialReader(self.serial_port)
            self.reader_thread.data_received.connect(self.terminal_output.append)
            self.reader_thread.start()
        except serial.SerialException as e:
            self.terminal_output.append(f"Ошибка подключения: {e}")
            self.serial_port = None

    def save_settings(self, port, baud):
        with open(CONFIG_FILE, "w") as f:
            json.dump({"port": port, "baudrate": baud}, f)
    
    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            ports = [p.device for p in serial.tools.list_ports.comports()]
            if cfg.get("port") in ports:
                self.port_combo.setCurrentText(cfg["port"])
            if str(cfg.get("baudrate")) in [self.baud_combo.itemText(i) for i in range(self.baud_combo.count())]:
                self.baud_combo.setCurrentText(str(cfg["baudrate"]))
    
    def send_data(self):
        if not (self.serial_port and self.serial_port.is_open):
            return
        # применяем лимит
        val = int(self.current_data["speed"] * self.current_data["limit"])
        speed = val
        b = 1 if self.current_data["brake"] else 0
        # используем одну скорость (на оба мотора) и реверс
        packet = f"S{speed};B{b}\n"
        self.terminal_output.append(f"Отправка: {packet.strip()}")
        self.serial_port.write(packet.encode('utf-8'))
    
    def update_speed(self, val):
        self.current_data["speed"] = val
        self.speed_label.setText(f"Скорость: {val}")
        self.send_data()
    def reset_speed(self):
        self.speed_slider.setValue(0)
    
    def update_limit(self, val):
        limits = {1:0.2,2:0.4,3:0.6,4:0.8,5:1.0}
        self.current_data["limit"] = limits[val]
        self.limit_label.setText(f"Мощность: {int(limits[val]*100)}%")
        self.send_data()
    
    def press_brake(self):
        self.current_data["brake"] = True
        self.brake_button.setStyleSheet("background-color: red;")
        self.send_data()
    def release_brake(self):
        self.current_data["brake"] = False
        self.brake_button.setStyleSheet("")
        self.send_data()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SerialController()
    window.show()
    sys.exit(app.exec())
