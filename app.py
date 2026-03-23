import os
import re
import json
import shutil
import base64
import zipfile
import tarfile
import sqlite3
import threading
import requests
import mmap
import queue
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import customtkinter as ctk
from tkinter import filedialog, messagebox

try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

class UltimateExtractorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Ultimate Media Extractor (Multi-Core)")
        self.geometry("800x620")
        self.resizable(False, False)

        self.source_path = ctk.StringVar()
        self.dest_path = ctk.StringVar()
        
        self.total_files = 0
        self.processed_files = 0
        self.extracted_count = 0
        
        self.log_queue = queue.Queue()
        self.is_running = False
        self.stop_requested = False # Флаг для остановки

        self.setup_ui()
        self.check_queue()

    def setup_ui(self):
        title = ctk.CTkLabel(self, text="Ультимативное извлечение файлов", font=ctk.CTkFont(size=22, weight="bold"))
        title.pack(pady=(15, 5))
        
        desc = ctk.CTkLabel(self, text="Использует все ядра ПК | Безопасная остановка | SQLite | Архивы", text_color="gray")
        desc.pack(pady=(0, 15))

        # Пути
        frame_paths = ctk.CTkFrame(self, fg_color="transparent")
        frame_paths.pack(fill="x", padx=20)

        ctk.CTkLabel(frame_paths, text="Источник:").grid(row=0, column=0, sticky="w", pady=5)
        ctk.CTkEntry(frame_paths, textvariable=self.source_path, width=500).grid(row=0, column=1, padx=10, pady=5)
        ctk.CTkButton(frame_paths, text="Обзор", command=self.browse_source, width=100).grid(row=0, column=2, pady=5)

        ctk.CTkLabel(frame_paths, text="Сохранить в:").grid(row=1, column=0, sticky="w", pady=5)
        ctk.CTkEntry(frame_paths, textvariable=self.dest_path, width=500).grid(row=1, column=1, padx=10, pady=5)
        ctk.CTkButton(frame_paths, text="Обзор", command=self.browse_dest, width=100).grid(row=1, column=2, pady=5)

        # Настройки
        self.frame_options = ctk.CTkFrame(self)
        self.frame_options.pack(fill="x", padx=20, pady=15)
        
        self.opt_network = ctk.CTkCheckBox(self.frame_options, text="Скачивать по ссылкам")
        self.opt_network.select()
        self.opt_network.pack(side="left", padx=20, pady=10)

        self.opt_carving = ctk.CTkCheckBox(self.frame_options, text="Глубокий поиск (Carving)")
        self.opt_carving.select()
        self.opt_carving.pack(side="left", padx=20, pady=10)

        # Прогресс
        self.progress_bar = ctk.CTkProgressBar(self, width=760)
        self.progress_bar.pack(pady=5)
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(self, text="Ожидание...", font=ctk.CTkFont(weight="bold"))
        self.status_label.pack()

        # Консоль логов
        self.log_box = ctk.CTkTextbox(self, width=760, height=150, state="disabled", fg_color="#1e1e1e", text_color="#00ff00")
        self.log_box.pack(pady=10)

        # Кнопки управления
        frame_btns = ctk.CTkFrame(self, fg_color="transparent")
        frame_btns.pack(pady=10)

        self.btn_start = ctk.CTkButton(frame_btns, text="🚀 ЗАПУСТИТЬ", command=self.start_extraction, height=45, width=250, font=ctk.CTkFont(size=16, weight="bold"))
        self.btn_start.pack(side="left", padx=10)

        self.btn_stop = ctk.CTkButton(frame_btns, text="🛑 СТОП", command=self.stop_extraction, height=45, width=250, fg_color="#990000", hover_color="#cc0000", font=ctk.CTkFont(size=16, weight="bold"), state="disabled")
        self.btn_stop.pack(side="left", padx=10)

    def log(self, message):
        self.log_queue.put(message)

    def check_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            
            if self.total_files > 0:
                progress = self.processed_files / self.total_files
                self.progress_bar.set(progress)
                self.status_label.configure(text=f"Обработано: {self.processed_files} / {self.total_files} | Найдено: {self.extracted_count}")

        self.after(100, self.check_queue)

    def browse_source(self):
        path = filedialog.askdirectory() or filedialog.askopenfilename()
        if path: self.source_path.set(path)

    def browse_dest(self):
        path = filedialog.askdirectory()
        if path: self.dest_path.set(path)

    def stop_extraction(self):
        """Метод для запроса остановки"""
        if self.is_running:
            self.stop_requested = True
            self.log("\n[!] ЗАПРОС НА ОСТАНОВКУ... Завершаем текущие процессы...")
            self.btn_stop.configure(state="disabled")

    def start_extraction(self):
        src = self.source_path.get()
        dest = self.dest_path.get()

        if not src or not dest:
            messagebox.showwarning("Ошибка", "Укажите пути!")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        
        self.total_files = 0
        self.processed_files = 0
        self.extracted_count = 0
        self.is_running = True
        self.stop_requested = False

        threading.Thread(target=self.run_manager, args=(src, dest), daemon=True).start()

    def run_manager(self, src, dest):
        self.log("[*] Сканирование файлов...")
        files_to_process = []
        
        if os.path.isfile(src):
            files_to_process.append(src)
        else:
            for root, _, files in os.walk(src):
                if self.stop_requested: break
                for f in files:
                    files_to_process.append(os.path.join(root, f))

        self.total_files = len(files_to_process)
        self.log(f"[*] Найдено: {self.total_files}. Запуск потоков...")

        max_workers = min(32, (os.cpu_count() or 1) * 4)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for f in files_to_process:
                if self.stop_requested: break
                futures.append(executor.submit(self.process_single_file, f, dest))
            
            for future in futures:
                if self.stop_requested:
                    # Пытаемся отменить еще не запущенные задачи
                    future.cancel()
                    continue
                
                try:
                    self.extracted_count += future.result()
                    self.processed_files += 1
                except Exception as e:
                    self.log(f"[!] Ошибка: {e}")

        if self.stop_requested:
            self.log("\n[!] ПРОЦЕСС ПРЕРВАН ПОЛЬЗОВАТЕЛЕМ.")
        else:
            self.log("\n[+] ИЗВЛЕЧЕНИЕ ЗАВЕРШЕНО!")
            
        self.is_running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def process_single_file(self, file_path, dest):
        """Обработка одного файла с проверкой флага остановки"""
        if self.stop_requested: return 0
        
        extracted = 0
        filename = os.path.basename(file_path)
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        
        try:
            # 1. Прямое копирование
            if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'mp4', 'mp3', 'avi']:
                shutil.copy(file_path, os.path.join(dest, f"copy_{uuid.uuid4().hex[:5]}_{filename}"))
                return 1

            # 2. SQLite
            if ext in ['sqlite', 'sqlite3', 'db']:
                extracted += self.extract_from_sqlite(file_path, dest)

            # 3. Архивы
            elif ext in ['zip', 'docx', 'xlsx', 'apk']:
                extracted += self.extract_from_zip(file_path, dest)

            # 4. Текстовые (JSON, HTML)
            elif ext in ['json', 'html', 'htm', 'txt', 'js', 'css']:
                extracted += self.extract_from_text(file_path, dest)

            # 5. Carving (Бинарный поиск)
            if self.opt_carving.get() == 1 and not self.stop_requested:
                extracted += self.carve_with_mmap(file_path, dest)
        except:
            pass

        return extracted

    def extract_from_sqlite(self, file_path, dest):
        count = 0
        try:
            conn = sqlite3.connect(f"file:{file_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            for table in tables:
                if self.stop_requested: break
                cursor.execute(f"SELECT * FROM {table[0]}")
                for row in cursor.fetchall():
                    if self.stop_requested: break
                    for item in row:
                        if isinstance(item, bytes):
                            if item.startswith(b'\xff\xd8\xff'):
                                self.save_bytes(item, dest, "jpg")
                                count += 1
                            elif item.startswith(b'\x89PNG\r\n\x1a\n'):
                                self.save_bytes(item, dest, "png")
                                count += 1
            conn.close()
        except: pass
        return count

    def carve_with_mmap(self, file_path, dest):
        count = 0
        try:
            with open(file_path, 'rb') as f:
                size = os.path.getsize(file_path)
                if size == 0: return 0
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    for sig, ext in [(b'\xff\xd8\xff', 'jpg'), (b'\x89PNG\r\n\x1a\n', 'png')]:
                        if self.stop_requested: break
                        start = 0
                        while True:
                            if self.stop_requested: break
                            start = mm.find(sig, start)
                            if start == -1: break
                            # Для JPEG ищем конец
                            end = mm.find(b'\xff\xd9', start) if ext == 'jpg' else mm.find(b'IEND\xaeB`\x82', start)
                            if end != -1:
                                end += (2 if ext == 'jpg' else 8)
                                self.save_bytes(mm[start:end], dest, ext)
                                count += 1
                                start = end
                            else: break
        except: pass
        return count

    def extract_from_text(self, file_path, dest):
        count = 0
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                # Base64
                for match in re.finditer(r"data:image/(?P<ext>\w+);base64,(?P<data>[A-Za-z0-9+/=]+)", content):
                    if self.stop_requested: break
                    try:
                        self.save_bytes(base64.b64decode(match.group('data')), dest, match.group('ext').replace('jpeg','jpg'))
                        count += 1
                    except: pass
                # Links
                if self.opt_network.get() == 1:
                    urls = re.findall(r"https?://[^\s\"\'<>]+?\.(?:jpg|jpeg|png|gif|webp)", content, re.I)
                    for url in set(urls):
                        if self.stop_requested: break
                        if self.download_image(url, dest): count += 1
        except: pass
        return count

    def extract_from_zip(self, file_path, dest):
        count = 0
        try:
            with zipfile.ZipFile(file_path, 'r') as z:
                for info in z.infolist():
                    if self.stop_requested: break
                    if info.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                        z.extract(info, dest)
                        count += 1
        except: pass
        return count

    def download_image(self, url, dest):
        try:
            resp = requests.get(url, stream=True, timeout=5)
            if resp.status_code == 200:
                ext = url.split('.')[-1][:4]
                filename = f"net_{uuid.uuid4().hex[:8]}.{ext}"
                with open(os.path.join(dest, filename), 'wb') as f:
                    for chunk in resp.iter_content(8192):
                        if self.stop_requested: return False
                        f.write(chunk)
                return True
        except: pass
        return False

    def save_bytes(self, data, dest, ext):
        filename = f"ext_{uuid.uuid4().hex[:10]}.{ext}"
        with open(os.path.join(dest, filename), 'wb') as f:
            f.write(data)

if __name__ == "__main__":
    app = UltimateExtractorApp()
    app.mainloop()
