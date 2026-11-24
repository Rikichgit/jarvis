# -*- coding: utf-8 -*-
"""
Джарвис — офлайн голосовое управление ПК (Windows) + голосовые макросы.

Установка:
    pip install vosk sounddevice pyautogui opencv-python pillow pyperclip

Рядом со скриптом:
    model/            папка модели Vosk (внутри am, conf, graph ...) — ищется автоматически
    macros/           создаётся автоматически, тут лежат макросы (по одному .json на макрос)
"""

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
import winsound

# ---------------- ПУТИ И НАСТРОЙКИ ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MACROS_DIR = os.path.join(BASE_DIR, "macros")

SAMPLE_RATE = 16000
WAKE_WORDS = ["джарвис", "жарвис", "джарви", "джарвиз", "джервис", "джарвес", "чарвис", "дарвис"]
ARMED_SECONDS = 6


# ---------------- ПРАВА АДМИНИСТРАТОРА ----------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


if not is_admin():
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit()

# Чтобы координаты кликов и скриншоты не "плыли" при масштабировании Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk  # noqa: E402
from tkinter import filedialog, messagebox, ttk  # noqa: E402

import pyautogui  # noqa: E402
import pyperclip  # noqa: E402
import sounddevice as sd  # noqa: E402
from vosk import KaldiRecognizer, Model, SetLogLevel  # noqa: E402

SetLogLevel(-1)
pyautogui.PAUSE = 0.05  # FAILSAFE оставлен включённым: мышь в левый верхний угол прерывает макрос

# ---------------- ЖУРНАЛ ----------------
LOG_Q = queue.Queue()


def say(text):
    print(f"[Джарвис] {text}")
    LOG_Q.put(text)


# ---------------- БАЗОВЫЕ ДЕЙСТВИЯ ----------------
def normalize(text):
    return text.lower().replace("ё", "е").strip()


def open_url(url):
    webbrowser.open(url)


def wait_for_image(path, timeout):
    """Ждёт появления картинки на экране, возвращает Point или None."""
    if not os.path.exists(path):
        return None
    deadline = time.time() + timeout
    while True:
        try:
            pos = pyautogui.locateCenterOnScreen(path, confidence=0.8)
        except Exception:
            pos = None
        if pos:
            return pos
        if time.time() >= deadline:
            return None
        time.sleep(1)


# ---------------- МАКРОСЫ: ХРАНЕНИЕ ----------------
def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "macro"


def load_macros():
    macros = []
    if not os.path.isdir(MACROS_DIR):
        return macros
    for fn in sorted(os.listdir(MACROS_DIR)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(MACROS_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["_file"] = path
            macros.append(data)
        except Exception as e:
            say(f"Не удалось прочитать макрос {fn}: {e}")
    return macros


def save_macro(macro, old_path=None):
    os.makedirs(MACROS_DIR, exist_ok=True)
    path = os.path.join(MACROS_DIR, safe_filename(macro["name"]) + ".json")
    data = {k: v for k, v in macro.items() if k != "_file"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if old_path and os.path.exists(old_path) and os.path.normcase(old_path) != os.path.normcase(path):
        os.remove(old_path)
    return path


# ---------------- МАКРОСЫ: ТИПЫ ДЕЙСТВИЙ ----------------
ACTION_LABELS = {
    "launch": "Запустить файл / программу",
    "url": "Открыть сайт",
    "click": "Клик по координатам",
    "click_image": "Клик по картинке на экране",
    "type": "Ввести текст",
    "keys": "Нажать клавиши",
    "wait": "Пауза",
    "cmd": "Команда cmd",
}

# (поле, подпись, тип поля, варианты)
SPECS = {
    "launch": [("path", "Файл или программа", "file", None),
               ("args", "Аргументы (необязательно)", "str", None)],
    "url": [("url", "Адрес сайта", "str", None)],
    "click": [("x", "X", "int", None), ("y", "Y", "int", None),
              ("button", "Кнопка мыши", "choice", ["левая", "правая", "средняя"]),
              ("clicks", "Количество кликов", "choice", ["1", "2"])],
    "click_image": [("path", "Картинка (PNG)", "file", None),
                    ("timeout", "Ждать появления, сек", "float", None)],
    "type": [("text", "Текст", "str", None),
             ("enter", "Нажать Enter после ввода", "bool", None)],
    "keys": [("keys", "Клавиши через +", "str", None)],
    "wait": [("seconds", "Секунд", "float", None)],
    "cmd": [("command", "Команда", "str", None)],
}

DEFAULTS = {
    "launch": {"path": "", "args": ""},
    "url": {"url": "https://"},
    "click": {"x": 0, "y": 0, "button": "левая", "clicks": "1"},
    "click_image": {"path": "", "timeout": 10},
    "type": {"text": "", "enter": False},
    "keys": {"keys": ""},
    "wait": {"seconds": 1},
    "cmd": {"command": ""},
}

HINTS = {
    "type": "Текст вставляется через буфер обмена (русский язык поддерживается).",
    "keys": "Примеры: win+d, ctrl+shift+esc, alt+f4, enter",
    "click_image": "Скрипт ищет картинку на экране и кликает по её центру.",
    "cmd": "Например: shutdown /s /t 0 (выключить компьютер)",
    "click": "Нажмите кнопку ниже: экран затемнится, кликните в нужную точку (Esc — отмена).",
}

OPTIONAL_FIELDS = {"args"}
BUTTON_MAP = {"левая": "left", "правая": "right", "средняя": "middle"}


def describe_action(a):
    t = a.get("type")
    if t == "launch":
        p = a["path"] + (" " + a["args"] if a.get("args") else "")
    elif t == "url":
        p = a["url"]
    elif t == "click":
        p = f'({a["x"]}, {a["y"]}), {a["button"]}, кликов: {a["clicks"]}'
    elif t == "click_image":
        p = f'{a["path"]} (ждать {a["timeout"]} с)'
    elif t == "type":
        p = a["text"] + (" + Enter" if a.get("enter") else "")
    elif t == "keys":
        p = a["keys"]
    elif t == "wait":
        p = f'{a["seconds"]} с'
    elif t == "cmd":
        p = a["command"]
    else:
        p = ""
    return ACTION_LABELS.get(t, str(t)), p


# ---------------- МАКРОСЫ: ВЫПОЛНЕНИЕ ----------------
def run_action(a):
    t = a["type"]
    if t == "launch":
        path = a["path"]
        args = a.get("args", "")
        subprocess.Popen(f'start "" "{path}" {args}', shell=True,
                         cwd=os.path.dirname(path) or None)
    elif t == "url":
        open_url(a["url"])
    elif t == "click":
        pyautogui.click(int(a["x"]), int(a["y"]), clicks=int(a["clicks"]), interval=0.1,
                        button=BUTTON_MAP.get(a["button"], "left"))
    elif t == "click_image":
        pos = wait_for_image(a["path"], float(a["timeout"]))
        if not pos:
            raise RuntimeError(f'картинка не найдена на экране: {a["path"]}')
        pyautogui.click(pos.x, pos.y)
    elif t == "type":
        pyperclip.copy(a["text"])
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        if a.get("enter"):
            time.sleep(0.1)
            pyautogui.press("enter")
    elif t == "keys":
        pyautogui.hotkey(*[k.strip().lower() for k in a["keys"].split("+") if k.strip()])
    elif t == "wait":
        time.sleep(float(a["seconds"]))
    elif t == "cmd":
        subprocess.Popen(a["command"], shell=True)


def run_macro(macro):
    """Выполняет макрос. Возвращает текст ошибки или None."""
    name = macro.get("name", "?")
    actions = macro.get("actions", [])
    try:
        delay = float(macro.get("default_delay", 0.5))
    except (TypeError, ValueError):
        delay = 0.5
    say(f"Выполняю макрос «{name}»")
    try:
        for i, a in enumerate(actions):
            run_action(a)
            if i < len(actions) - 1 and a["type"] != "wait":
                time.sleep(delay)
    except Exception as e:
        say(f"Макрос «{name}» остановлен: {e}")
        return str(e)
    say(f"Макрос «{name}» выполнен")
    return None


def find_macro(cmd):
    """Ищет макрос, чья фраза встречается в команде (побеждает самая длинная фраза)."""
    best, best_len = None, 0
    for m in load_macros():
        for ph in m.get("phrases", []):
            p = normalize(ph)
            if p and len(p) > best_len and re.search(rf"(?<!\w){re.escape(p)}(?!\w)", cmd):
                best, best_len = m, len(p)
    return best


# ---------------- РАЗБОР КОМАНД ----------------
def find_wake_word(text):
    for w in WAKE_WORDS:
        m = re.search(rf"\b{w}\b", text)
        if m:
            return True, text[m.end():].strip()
    return False, ""


def handle_command(cmd):
    cmd = normalize(cmd)
    if not cmd:
        return False
    macro = find_macro(cmd)
    if macro:
        run_macro(macro)
        return True
    say(f"Макрос не найден для фразы: «{cmd}»")
    return False


def run_async(cmd):
    threading.Thread(target=handle_command, args=(cmd,), daemon=True).start()


# ---------------- ПРОСЛУШИВАНИЕ ----------------
_MODEL = None


def is_model_dir(p):
    return os.path.isdir(os.path.join(p, "am")) and os.path.isdir(os.path.join(p, "conf"))


def find_model():
    candidates = [os.path.join(BASE_DIR, "model"), BASE_DIR]
    try:
        for d in os.listdir(BASE_DIR):
            if d.lower().startswith("vosk-model"):
                candidates.append(os.path.join(BASE_DIR, d))
    except OSError:
        pass
    for c in candidates:
        if is_model_dir(c):
            return c
        if os.path.isdir(c):  # лишняя вложенная папка после распаковки
            for sub in os.listdir(c):
                p = os.path.join(c, sub)
                if os.path.isdir(p) and is_model_dir(p):
                    return p
    return None


class Listener(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.status = "Загрузка модели..."

    def run(self):
        global _MODEL
        try:
            if _MODEL is None:
                path = find_model()
                if not path:
                    say("Модель Vosk не найдена. Положите её в папку «model» рядом со скриптом.")
                    self.status = "Ошибка: нет модели"
                    return
                _MODEL = Model(path)

            rec = KaldiRecognizer(_MODEL, SAMPLE_RATE)
            audio_q = queue.Queue()

            def callback(indata, frames, time_info, status):
                audio_q.put(bytes(indata))

            armed_until = 0.0
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000, dtype="int16",
                                   channels=1, callback=callback):
                self.status = "Слушаю"
                say("Готов. Скажите: «Джарвис, <команда>»")
                while not self.stop_event.is_set():
                    try:
                        data = audio_q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if not rec.AcceptWaveform(data):
                        continue
                    text = normalize(json.loads(rec.Result()).get("text", ""))
                    if not text:
                        continue
                    say(f"Услышал: {text}")

                    found, cmd = find_wake_word(text)
                    if found:
                        winsound.Beep(1000, 120)
                        if cmd:
                            run_async(cmd)
                        else:
                            armed_until = time.time() + ARMED_SECONDS
                            say("Слушаю...")
                    elif time.time() < armed_until:
                        armed_until = 0.0
                        run_async(text)
            self.status = "Остановлено"
        except Exception as e:
            say(f"Ошибка: {e}")
            self.status = "Ошибка"


# ---------------- GUI: ДИАЛОГ ДЕЙСТВИЯ ----------------
class ActionDialog(tk.Toplevel):
    def __init__(self, parent, initial=None):
        super().__init__(parent)
        self.title("Действие")
        self.transient(parent)
        self.editor = parent
        self.result = None
        self.initial = initial or {}
        self.vars = {}

        start_key = self.initial.get("type", "launch")
        self.type_var = tk.StringVar(value=ACTION_LABELS[start_key])

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(top, text="Тип действия:").pack(side="left")
        cb = ttk.Combobox(top, textvariable=self.type_var, values=list(ACTION_LABELS.values()),
                          state="readonly", width=38)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self.build_fields())

        self.fields_frame = ttk.Frame(self)
        self.fields_frame.pack(fill="both", expand=True, padx=12, pady=6)
        self.fields_frame.columnconfigure(1, weight=1)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side="right", padx=6)

        self.build_fields()
        self.minsize(560, 280)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def current_key(self):
        for k, label in ACTION_LABELS.items():
            if label == self.type_var.get():
                return k
        return "launch"

    def build_fields(self):
        for w in self.fields_frame.winfo_children():
            w.destroy()
        key = self.current_key()
        self.vars = {}
        values = self.initial if self.initial.get("type") == key else DEFAULTS[key]

        row = 0
        for field, label, kind, opts in SPECS[key]:
            ttk.Label(self.fields_frame, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=5)
            if kind == "bool":
                var = tk.BooleanVar(value=bool(values.get(field, False)))
                ttk.Checkbutton(self.fields_frame, variable=var).grid(row=row, column=1, sticky="w")
            elif kind == "choice":
                var = tk.StringVar(value=str(values.get(field, opts[0])))
                ttk.Combobox(self.fields_frame, textvariable=var, values=opts, state="readonly",
                             width=14).grid(row=row, column=1, sticky="w")
            else:
                var = tk.StringVar(value=str(values.get(field, "")))
                ttk.Entry(self.fields_frame, textvariable=var).grid(row=row, column=1, sticky="we")
                if kind == "file":
                    ttk.Button(self.fields_frame, text="Обзор...",
                               command=lambda v=var, k=key: self.browse(v, k)).grid(row=row, column=2, padx=4)
            self.vars[field] = var
            row += 1

        if key == "click":
            ttk.Button(self.fields_frame, text="Выбрать точку на экране",
                       command=self.capture_point).grid(row=row, column=1, sticky="w", pady=6)
            row += 1
        if key in HINTS:
            ttk.Label(self.fields_frame, text=HINTS[key], foreground="#666666",
                      wraplength=480, justify="left").grid(row=row, column=0, columnspan=3,
                                                           sticky="w", padx=4, pady=(8, 0))

    def browse(self, var, key):
        if key == "click_image":
            types = [("Изображения", "*.png *.jpg *.jpeg *.bmp"), ("Все файлы", "*.*")]
        else:
            types = [("Все файлы", "*.*")]
        path = filedialog.askopenfilename(parent=self, filetypes=types)
        if path:
            var.set(os.path.normpath(path))

    def capture_point(self):
        """Полупрозрачный оверлей на весь экран: клик по нему записывает координаты."""
        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.3)
        overlay.configure(bg="black", cursor="crosshair")
        w, h = self.winfo_screenwidth(), self.winfo_screenheight()
        overlay.geometry(f"{w}x{h}+0+0")
        tk.Label(overlay, text="Кликните в нужную точку экрана (Esc — отмена)",
                 bg="black", fg="white", font=("Segoe UI", 18)).place(relx=0.5, rely=0.1, anchor="center")

        def finish(event=None):
            if event is not None:
                self.vars["x"].set(str(event.x_root))
                self.vars["y"].set(str(event.y_root))
                winsound.Beep(1200, 120)
            try:
                overlay.grab_release()
            except tk.TclError:
                pass
            overlay.destroy()
            self.lift()
            self.focus_force()
            try:
                self.grab_set()
            except tk.TclError:
                pass

        overlay.bind("<Button-1>", finish)
        overlay.bind("<Escape>", lambda e: finish(None))
        overlay.update_idletasks()
        overlay.focus_force()
        try:
            overlay.grab_set()
        except tk.TclError:
            pass

    def on_ok(self):
        key = self.current_key()
        action = {"type": key}
        for field, label, kind, opts in SPECS[key]:
            v = self.vars[field].get()
            if kind == "int":
                try:
                    action[field] = int(str(v).strip())
                except ValueError:
                    messagebox.showerror("Ошибка", f"Поле «{label}» должно быть целым числом.", parent=self)
                    return
            elif kind == "float":
                try:
                    num = float(str(v).replace(",", ".").strip())
                    if num < 0:
                        raise ValueError
                    action[field] = num
                except ValueError:
                    messagebox.showerror("Ошибка", f"Поле «{label}» должно быть числом (0 или больше).",
                                         parent=self)
                    return
            elif kind == "bool":
                action[field] = bool(v)
            else:
                v = str(v).strip()
                if not v and field not in OPTIONAL_FIELDS:
                    messagebox.showerror("Ошибка", f"Поле «{label}» не заполнено.", parent=self)
                    return
                action[field] = v

        if key == "keys":
            parts = [p.strip().lower() for p in action["keys"].split("+") if p.strip()]
            bad = [p for p in parts if p not in pyautogui.KEYBOARD_KEYS]
            if not parts or bad:
                messagebox.showerror("Ошибка", "Неизвестные клавиши: " + (", ".join(bad) or "—") +
                                     "\nПример: ctrl+shift+esc", parent=self)
                return
            action["keys"] = "+".join(parts)

        if key in ("launch", "click_image") and not os.path.exists(action["path"]):
            if key == "click_image":
                messagebox.showerror("Ошибка", "Файл картинки не найден.", parent=self)
                return
            if not messagebox.askyesno("Файл не найден",
                                       "Такого файла нет. Добавить действие всё равно?\n"
                                       "(подойдёт для имён вроде notepad.exe)", parent=self):
                return

        self.result = action
        self.destroy()


# ---------------- GUI: РЕДАКТОР МАКРОСА ----------------
class MacroEditor(tk.Toplevel):
    def __init__(self, parent, macro=None, on_saved=None):
        super().__init__(parent)
        self.title("Создание макроса" if macro is None else "Редактирование макроса")
        self.geometry("800x720")
        self.on_saved = on_saved
        self.old_path = macro.get("_file") if macro else None
        self.phrases = list(macro.get("phrases", [])) if macro else []
        self.actions = [dict(a) for a in macro.get("actions", [])] if macro else []
        self.name_var = tk.StringVar(value=macro.get("name", "") if macro else "")
        self.delay_var = tk.StringVar(value=str(macro.get("default_delay", 0.5)) if macro else "0.5")
        self.phrase_var = tk.StringVar()

        # --- название и пауза
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(top, text="Название макроса:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.name_var, width=45).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Пауза между действиями по умолчанию, сек:").grid(row=1, column=0,
                                                                              sticky="w", pady=(8, 0))
        ttk.Spinbox(top, from_=0, to=60, increment=0.1, textvariable=self.delay_var,
                    width=8).grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))

        # --- таблица фраз
        pf = ttk.LabelFrame(self, text="Слова для активации (говорятся после «Джарвис»)")
        pf.pack(fill="x", padx=12, pady=8)
        self.phrase_tree = ttk.Treeview(pf, columns=("n", "phrase"), show="headings", height=5)
        self.phrase_tree.heading("n", text="№")
        self.phrase_tree.heading("phrase", text="Фраза")
        self.phrase_tree.column("n", width=50, anchor="center", stretch=False)
        self.phrase_tree.column("phrase", width=600)
        self.phrase_tree.pack(fill="x", padx=8, pady=(8, 4))
        row = ttk.Frame(pf)
        row.pack(fill="x", padx=8, pady=(0, 4))
        entry = ttk.Entry(row, textvariable=self.phrase_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.add_phrase())
        ttk.Button(row, text="Добавить", command=self.add_phrase).pack(side="left", padx=6)
        ttk.Button(row, text="Удалить выбранную", command=self.del_phrase).pack(side="left")
        ttk.Label(pf, foreground="#666666", wraplength=700, justify="left",
                  text="Пишите фразу так, как её слышит Vosk (строки «Услышал:» в журнале управления). "
                       "Фраза срабатывает, если встречается в сказанной команде.").pack(
            anchor="w", padx=8, pady=(0, 8))

        # --- действия
        af = ttk.LabelFrame(self, text="Череда действий")
        af.pack(fill="both", expand=True, padx=12, pady=4)
        self.action_tree = ttk.Treeview(af, columns=("n", "type", "params"), show="headings", height=10)
        self.action_tree.heading("n", text="№")
        self.action_tree.heading("type", text="Действие")
        self.action_tree.heading("params", text="Параметры")
        self.action_tree.column("n", width=40, anchor="center", stretch=False)
        self.action_tree.column("type", width=220, stretch=False)
        self.action_tree.column("params", width=440)
        self.action_tree.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.action_tree.bind("<Double-1>", lambda e: self.edit_action())
        ab = ttk.Frame(af)
        ab.pack(fill="x", padx=8, pady=(0, 8))
        for text, cmd in [("Добавить", self.add_action), ("Изменить", self.edit_action),
                          ("Удалить", self.del_action), ("↑ Вверх", lambda: self.move(-1)),
                          ("↓ Вниз", lambda: self.move(1))]:
            ttk.Button(ab, text=text, command=cmd).pack(side="left", padx=(0, 6))

        # --- низ
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=12, pady=12)
        ttk.Button(bottom, text="Сохранить макрос", command=self.save).pack(side="right")
        ttk.Button(bottom, text="Отмена", command=self.destroy).pack(side="right", padx=6)

        self.refresh_phrases()
        self.refresh_actions()

    # фразы
    def refresh_phrases(self):
        self.phrase_tree.delete(*self.phrase_tree.get_children())
        for i, p in enumerate(self.phrases):
            self.phrase_tree.insert("", "end", iid=str(i), values=(i + 1, p))

    def add_phrase(self):
        p = self.phrase_var.get().strip()
        if p and normalize(p) not in [normalize(x) for x in self.phrases]:
            self.phrases.append(p)
            self.refresh_phrases()
        self.phrase_var.set("")

    def del_phrase(self):
        sel = self.phrase_tree.selection()
        if sel:
            del self.phrases[int(sel[0])]
            self.refresh_phrases()

    # действия
    def refresh_actions(self, select=None):
        self.action_tree.delete(*self.action_tree.get_children())
        for i, a in enumerate(self.actions):
            label, params = describe_action(a)
            self.action_tree.insert("", "end", iid=str(i), values=(i + 1, label, params))
        if select is not None and 0 <= select < len(self.actions):
            self.action_tree.selection_set(str(select))

    def selected_action(self):
        sel = self.action_tree.selection()
        return int(sel[0]) if sel else None

    def add_action(self):
        dlg = ActionDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            idx = self.selected_action()
            pos = idx + 1 if idx is not None else len(self.actions)
            self.actions.insert(pos, dlg.result)
            self.refresh_actions(pos)

    def edit_action(self):
        idx = self.selected_action()
        if idx is None:
            return
        dlg = ActionDialog(self, self.actions[idx])
        self.wait_window(dlg)
        if dlg.result:
            self.actions[idx] = dlg.result
            self.refresh_actions(idx)

    def del_action(self):
        idx = self.selected_action()
        if idx is not None:
            del self.actions[idx]
            self.refresh_actions(min(idx, len(self.actions) - 1))

    def move(self, d):
        idx = self.selected_action()
        if idx is None:
            return
        new = idx + d
        if 0 <= new < len(self.actions):
            self.actions[idx], self.actions[new] = self.actions[new], self.actions[idx]
            self.refresh_actions(new)

    # сохранение
    def save(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Ошибка", "Введите название макроса.", parent=self)
            return
        try:
            delay = float(self.delay_var.get().replace(",", "."))
            if delay < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Ошибка", "Пауза должна быть числом (0 или больше).", parent=self)
            return
        if not self.phrases:
            messagebox.showerror("Ошибка", "Добавьте хотя бы одну фразу для активации.", parent=self)
            return
        if not self.actions:
            messagebox.showerror("Ошибка", "Добавьте хотя бы одно действие.", parent=self)
            return

        path = os.path.join(MACROS_DIR, safe_filename(name) + ".json")
        same_file = self.old_path and os.path.normcase(path) == os.path.normcase(self.old_path)
        if os.path.exists(path) and not same_file:
            if not messagebox.askyesno("Макрос существует",
                                       "Макрос с таким названием уже есть. Перезаписать?", parent=self):
                return
        save_macro({"name": name, "phrases": self.phrases, "default_delay": delay,
                    "actions": self.actions}, self.old_path)
        if self.on_saved:
            self.on_saved()
        messagebox.showinfo("Готово", f"Макрос «{name}» сохранён в папке macros.", parent=self)
        self.destroy()


# ---------------- GUI: ПРОСМОТР МАКРОСОВ ----------------
class MacroViewer(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Мои макросы")
        self.geometry("960x540")
        self.macros = []

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 4))

        self.tree = ttk.Treeview(body, columns=("name", "phrases", "count"), show="headings")
        self.tree.heading("name", text="Название")
        self.tree.heading("phrases", text="Фразы")
        self.tree.heading("count", text="Шагов")
        self.tree.column("name", width=170)
        self.tree.column("phrases", width=220)
        self.tree.column("count", width=60, anchor="center", stretch=False)
        self.tree.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.show_details)

        self.details = tk.Text(body, wrap="word", state="disabled", width=50)
        self.details.pack(side="left", fill="both", expand=True, padx=(10, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=12)
        for text, cmd in [("Редактировать", self.edit), ("Запустить", self.run_selected),
                          ("Удалить", self.delete), ("Обновить", self.refresh)]:
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Закрыть", command=self.destroy).pack(side="right")

        self.refresh()

    def refresh(self):
        self.macros = load_macros()
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.macros):
            self.tree.insert("", "end", iid=str(i),
                             values=(m.get("name", ""), ", ".join(m.get("phrases", [])),
                                     len(m.get("actions", []))))
        self.set_details("Выберите макрос слева." if self.macros else
                         "Макросов пока нет. Создайте первый через «Создать макрос».")

    def set_details(self, text):
        self.details.config(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.config(state="disabled")

    def selected(self):
        sel = self.tree.selection()
        return self.macros[int(sel[0])] if sel else None

    def show_details(self, _event=None):
        m = self.selected()
        if not m:
            return
        lines = [f"Название: {m.get('name', '')}", "", "Фразы активации:"]
        lines += [f"  • {p}" for p in m.get("phrases", [])]
        lines += ["", f"Пауза между действиями: {m.get('default_delay', 0.5)} с",
                  f"Файл: {m.get('_file', '')}", "", "Действия:"]
        for i, a in enumerate(m.get("actions", []), 1):
            label, params = describe_action(a)
            lines.append(f"  {i}. {label} — {params}")
        self.set_details("\n".join(lines))

    def edit(self):
        m = self.selected()
        if not m:
            messagebox.showinfo("Макросы", "Сначала выберите макрос.", parent=self)
            return
        MacroEditor(self, m, on_saved=self.refresh)

    def delete(self):
        m = self.selected()
        if not m:
            messagebox.showinfo("Макросы", "Сначала выберите макрос.", parent=self)
            return
        if messagebox.askyesno("Удаление", f"Удалить макрос «{m.get('name', '')}»?", parent=self):
            try:
                os.remove(m["_file"])
            except OSError as e:
                messagebox.showerror("Ошибка", f"Не удалось удалить файл: {e}", parent=self)
            self.refresh()

    def run_selected(self):
        m = self.selected()
        if not m:
            messagebox.showinfo("Макросы", "Сначала выберите макрос.", parent=self)
            return
        if not messagebox.askyesno("Запуск", "Окна скроются, макрос запустится через 3 секунды. Продолжить?",
                                   parent=self):
            return
        self.withdraw()
        self.master.withdraw()
        done = threading.Event()
        result = []

        def work():
            time.sleep(3)
            result.append(run_macro(m))
            done.set()

        threading.Thread(target=work, daemon=True).start()
        self.after(500, self._wait_done, done, result)

    def _wait_done(self, done, result):
        if not done.is_set():
            self.after(500, self._wait_done, done, result)
            return
        self.master.deiconify()
        self.deiconify()
        if result and result[0]:
            messagebox.showerror("Макрос остановлен", result[0], parent=self)


# ---------------- GUI: ОКНО УПРАВЛЕНИЯ ----------------
class StatusWindow(tk.Toplevel):
    def __init__(self, launcher, listener):
        super().__init__(launcher)
        self.launcher = launcher
        self.listener = listener
        self.title("Джарвис — управление активно")
        self.geometry("680x440")

        self.status_lbl = ttk.Label(self, text="Статус: ...", font=("Segoe UI", 12, "bold"))
        self.status_lbl.pack(anchor="w", padx=12, pady=(12, 4))

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        self.log = tk.Text(frame, wrap="word", state="disabled")
        sb = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        ttk.Button(self, text="Остановить и вернуться в меню", command=self.stop).pack(pady=12, ipady=4)
        self.protocol("WM_DELETE_WINDOW", self.stop)
        self._job = None
        self.poll()

    def poll(self):
        try:
            while True:
                msg = LOG_Q.get_nowait()
                self.log.config(state="normal")
                self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
                self.log.see("end")
                self.log.config(state="disabled")
        except queue.Empty:
            pass
        self.status_lbl.config(text="Статус: " + self.listener.status)
        self._job = self.after(150, self.poll)

    def stop(self):
        self.listener.stop_event.set()
        if self._job:
            self.after_cancel(self._job)
        self.destroy()
        self.launcher.deiconify()


# ---------------- GUI: ГЛАВНОЕ МЕНЮ ----------------
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Джарвис")
        self.geometry("380x360")
        self.resizable(False, False)
        self.listener = None

        ttk.Label(self, text="Джарвис", font=("Segoe UI", 22, "bold")).pack(pady=(22, 2))
        ttk.Label(self, text="Выберите действие").pack(pady=(0, 14))
        for text, cmd in [("Активировать управление", self.start_listening),
                          ("Создать макрос", self.new_macro),
                          ("Мои макросы (просмотр / удаление)", self.open_viewer),
                          ("Выход", self.on_close)]:
            ttk.Button(self, text=text, command=cmd).pack(fill="x", padx=40, pady=5, ipady=6)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def start_listening(self):
        if self.listener and self.listener.is_alive():
            self.listener.stop_event.set()
        self.withdraw()
        self.listener = Listener()
        self.listener.start()
        StatusWindow(self, self.listener)

    def new_macro(self):
        MacroEditor(self)

    def open_viewer(self):
        MacroViewer(self)

    def on_close(self):
        if self.listener:
            self.listener.stop_event.set()
        self.destroy()


def main():
    os.makedirs(MACROS_DIR, exist_ok=True)
    Launcher().mainloop()


if __name__ == "__main__":
    main()
