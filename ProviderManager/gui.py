import tkinter as tk
from tkinter import ttk, messagebox
import threading, time, webbrowser, urllib.request, json, os, sys
from pathlib import Path
from provider_manager import ProviderController, PROVIDERS

LOG_PATH = Path(__file__).with_suffix(".log")

def log(msg: str):
    try:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def controller_action(controller, name, action_name, log_fn, notify=None):
    cancel_event = threading.Event()
    def worker():
        try:
            if action_name == "start":
                ok, detail = controller.start(name)
            elif action_name == "stop":
                ok, detail = controller.stop(name)
            elif action_name == "restart":
                ok, detail = controller.restart(name)
            elif action_name == "re_auth":
                cancel_event.clear()
                log_fn("Перелогин: открываю окно авторизации...")
                # Poll for status updates during re-auth
                poll_thread = threading.Thread(target=_poll_re_auth_status, args=(controller, name, log_fn, cancel_event), daemon=True)
                poll_thread.start()
                ok, detail = controller.re_auth(name)
                cancel_event.set()
                if ok:
                    log_fn("Перелогин: успешно.")
                else:
                    log_fn(f"Перелогин: {detail}")
                if notify:
                    if ok:
                        notify("Перелогин", "Авторизация обновлена.")
                    else:
                        notify("Перелогин", f"Не удалось: {detail}")
            else:
                ok, detail = False, "unknown"
            if action_name != "re_auth":
                msg = f"{action_name}: {detail}"
                log(msg)
                log_fn(msg)
        except Exception as e:
            log(f"{action_name} exception: {e}")
            log_fn(f"ошибка: {e}")
    threading.Thread(target=worker, daemon=True).start()
    return cancel_event

def _poll_re_auth_status(controller, name, log_fn, cancel_event):
    start = time.time()
    last = ""
    while not cancel_event.is_set():
        try:
            ok, body = controller.is_healthy(name)
            status = "OK" if ok else "ожидание..."
            msg = f"Перелогин: {status} ({int(time.time() - start)} сек)"
            if msg != last:
                log_fn(msg)
                last = msg
            if ok:
                return
        except Exception:
            pass
        time.sleep(1)


def refresh_card(controller, name, status_var, health_var, recovery_var, notify=None):
    def worker():
        try:
            status = controller.get_status(name)
            healthy = status["healthy"]
            running = status["running"]
            detail = status.get("detail", "")
            status_var.set("Процесс: запущен" if running else "Процесс: не запущен")
            if healthy:
                health_var.set(f"Здоровье: OK\n{detail}")
                recovery_var.set("")
            else:
                health_var.set(f"Здоровье: DOWN\n{detail}")
                recs = controller.suggest_recovery(name)
                tips = []
                if "re_auth" in recs:
                    tips.append("Совет: обновить авторизацию DeepSeek (кнопка «Перелогин»).")
                if "start" in recs:
                    tips.append("Совет: запустить прокси заново.")
                recovery_var.set("\n".join(tips))
        except Exception as e:
            health_var.set(f"Ошибка опроса: {e}")
    threading.Thread(target=worker, daemon=True).start()

def build_card(parent, name, controller, log_fn, notify=None):
    info = PROVIDERS[name]
    card = ttk.LabelFrame(parent, text=info["name"], padding=12)
    card.columnconfigure(0, weight=1)

    status_var = tk.StringVar(value="Статус: не проверен")
    health_var = tk.StringVar(value="—")
    recovery_var = tk.StringVar(value="")
    log_fn(f"Карточка {info['name']}: OK")

    ttk.Label(card, textvariable=status_var, foreground="#555").grid(row=0, column=0, sticky="w")
    ttk.Label(card, textvariable=health_var, foreground="#555", wraplength=520).grid(row=1, column=0, sticky="w")
    ttk.Label(card, textvariable=recovery_var, foreground="#8a6d3b", wraplength=520).grid(row=2, column=0, sticky="w", pady=(6, 0))

    btn_frame = ttk.Frame(card)
    btn_frame.grid(row=3, column=0, sticky="w", pady=(8, 0))
    ttk.Button(btn_frame, text="▶ Старт", command=lambda: controller_action(controller, name, "start", log_fn, notify)).pack(side="left", padx=(0, 6))
    ttk.Button(btn_frame, text="■ Стоп", command=lambda: controller_action(controller, name, "stop", log_fn, notify)).pack(side="left", padx=(0, 6))
    ttk.Button(btn_frame, text="↻ Рестарт", command=lambda: controller_action(controller, name, "restart", log_fn, notify)).pack(side="left", padx=(0, 6))
    if name == "deepseek" or name == "qwen":
        ttk.Button(btn_frame, text="🔑 Перелогин", command=lambda: controller_action(controller, name, "re_auth", log_fn, notify)).pack(side="left", padx=(0, 6))

    url = f"http://127.0.0.1:{info['port']}"
    link = ttk.Label(card, text=f"Открыть {url}", foreground="#0066cc", cursor="hand2")
    link.grid(row=4, column=0, sticky="w", pady=(6, 0))
    link.bind("<Button-1>", lambda e: webbrowser.open(url))

    return card, status_var, health_var, recovery_var

def build():
    root = tk.Tk()
    root.title("Provider Manager — Qwen / DeepSeek")
    root.geometry("640x420")

    controller = ProviderController()

    log_var = tk.StringVar(value="")

    def notify(title, body):
        try:
            messagebox.showinfo(title, body)
        except Exception:
            pass

    header = ttk.Label(root, text="Локальные прокси DeepSeek и Qwen", font=("Segoe UI", 12, "bold"))
    header.pack(padx=10, pady=(10, 0), anchor="w")

    ttk.Label(root, textvariable=log_var, foreground="#555", wraplength=620).pack(fill="x", padx=10, pady=(2, 0))

    cards = ttk.Frame(root)
    cards.pack(fill="both", expand=True, padx=10, pady=6)
    cards.columnconfigure(0, weight=1)
    cards.columnconfigure(1, weight=1)

    qwen_card, qwen_status, qwen_health, qwen_rec = build_card(cards, "qwen", controller, log_var.set, notify)
    qwen_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    ds_card, ds_status, ds_health, ds_rec = build_card(cards, "deepseek", controller, log_var.set, notify)
    ds_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

    bottom = ttk.Frame(root)
    bottom.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(bottom, text="Обновить статус", command=lambda: refresh_all()).pack(side="left")
    ttk.Button(bottom, text="Проверить всё", command=lambda: verify_all()).pack(side="left", padx=(6, 0))
    ttk.Button(bottom, text="Открыть SOP",
               command=lambda: webbrowser.open(str(Path(r"D:\Arx\Software Downloads\Hermes copy\hermes-dev\PROVIDERS_SOP.md")))
               ).pack(side="right")

    def refresh_all():
        refresh_card(controller, "qwen", qwen_status, qwen_health, qwen_rec, notify)
        refresh_card(controller, "deepseek", ds_status, ds_health, ds_rec, notify)

    def verify_all():
        for name, status_var, health_var in (("qwen", qwen_status, qwen_health),
                                               ("deepseek", ds_status, ds_health)):
            try:
                ok, body = controller.is_healthy(name)
                if ok:
                    messagebox.showinfo("Проверка", f"{PROVIDERS[name]['name']}: OK")
                else:
                    messagebox.showwarning("Проверка", f"{PROVIDERS[name]['name']}: DOWN\n{body}")
            except Exception as e:
                messagebox.showerror("Проверка", f"{PROVIDERS[name]['name']}: {e}")

    root.after(600, refresh_all)
    root.mainloop()

if __name__ == "__main__":
    build()
