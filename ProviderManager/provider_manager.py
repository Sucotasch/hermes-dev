import subprocess, shlex, json, os, time, re, sys
from pathlib import Path

PROVIDERS = {
    "qwen": {
        "name": "Qwen (FreeQwenApi)",
        "dir": r"D:\Arx\Software Downloads\Hermes copy\FreeQwenApi",
        "runtime": "D:\\Works\\Python\\python.exe",
        "start_cmd": ["D:\\Works\\Python\\python.exe", "qwen_light_proxy.py"],
        "stop_cmd": ["Qwen", "Proxy", "Stop.bat"],
        "health_url": "http://127.0.0.1:3264/api/health",
        "health_text": ['"ok":true', '"account_loaded":true'],
        "health_fail_text": ["-block"],
        "port": 3264,
        "alias": "custom:freeqwen",
        "default_model": "qwen3.7-max",
        "token_path": r"D:\Arx\Software Downloads\Hermes copy\FreeQwenApi\session\tokens.json",
        "re_auth": {
            "runtime": "node",
            "cmd": ["node", "scripts/qwen_chrome_auth.cjs"],
            "env": {
                "CHROME_PATH": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                "QWEN_REUSE_CHROME": "1",
                "QWEN_KEEP_CHROME_PROFILE": "1",
            },
        },
    },
    "deepseek": {
        "name": "DeepSeek (FreeDeepseekAPI)",
        "dir": r"D:\Arx\Software Downloads\Hermes copy\FreeDeepseekAPI",
        "runtime": "node",
        "start_cmd": [
            "node",
            "server.js",
        ],
        "stop_cmd": ["node", "scripts", "stop.js"],
        "health_url": "http://127.0.0.1:9655/",
        "health_text": ['"status":"ok"', '"config_ready":true'],
        "port": 9655,
        "alias": "custom:freedeep",
        "default_model": "deepseek-chat",
        "auth_file": r"D:\Arx\Software Downloads\Hermes copy\FreeDeepseekAPI\deepseek-auth.json",
        "re_auth": {
            "runtime": "node",
            "cmd": ["node", "scripts/deepseek_chrome_auth.js"],
            "env": {
                "CHROME_PATH": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                "DEEPSEEK_REUSE_CHROME_PROFILE": "1",
                "DEEPSEEK_KEEP_CHROME_PROFILE": "1",
            },
        },
    },
}


class ProviderController:
    def __init__(self, state_path=None):
        self.state_path = state_path or str(Path(__file__).with_suffix(".state.json"))
        self._load_state()

    def _load_state(self):
        if Path(self.state_path).exists():
            self.state = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
        else:
            self.state = {"qwen": {"running": False, "proc": None, "retries": 0},
                          "deepseek": {"running": False, "proc": None, "retries": 0}}
        # normalize
        for k in ("qwen", "deepseek"):
            if k not in self.state or not isinstance(self.state[k], dict):
                self.state[k] = {"running": False, "proc": None, "retries": 0}

    def _save_state(self):
        out = {}
        for k in ("qwen", "deepseek"):
            v = dict(self.state.get(k, {}))
            v.pop("proc", None)
            out[k] = v
        Path(self.state_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    def port_in_use(self, name):
        return self.is_healthy(name)

    @staticmethod
    def _port_listening(port: int) -> bool:
        if sys.platform != "win32" or not port:
            return False
        try:
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            ).stdout or ""
        except Exception:
            return False
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                return True
        return False

    @staticmethod
    def _kill_port(port: int):
        """Kill Windows processes listening on the given port."""
        if sys.platform != "win32" or not port:
            return
        try:
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            ).stdout or ""
        except Exception:
            return
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    pids.add(parts[-1])
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def is_healthy(self, name):
        info = PROVIDERS[name]
        # 1) Proxy health endpoint
        try:
            import urllib.request
            with urllib.request.urlopen(info["health_url"], timeout=3) as r:
                body = r.read(4096).decode("utf-8", errors="ignore")
            for needle in info.get("health_fail_text", []):
                if needle in body:
                    return False, body
            for needle in info.get("health_text", ["ok"]):
                if needle not in body:
                    return False, body
        except Exception as e:
            return False, str(e)

        # 2) Token validation (best-effort)
        token_path = info.get("token_path") or info.get("auth_file")
        if token_path:
            try:
                raw = Path(token_path).read_text(encoding="utf-8")
                data = json.loads(raw)
                # Qwen: array of token objects; DeepSeek: single object
                token_obj = None
                if isinstance(data, list):
                    valid = [t for t in data if not t.get("invalid")]
                    token_obj = valid[0] if valid else None
                elif isinstance(data, dict):
                    token_obj = None if data.get("invalid") else data
                if not token_obj:
                    return False, "token_invalid"
                token = token_obj.get("token") or token_obj.get("access_token") or ""
                if len(token) < 10:
                    return False, "token_too_short"
            except Exception:
                return False, "token_read_error"

        return True, body

    def start(self, name):
        info = PROVIDERS[name]
        port = info["port"]
        if self._port_listening(port):
            self._kill_port(port)
            time.sleep(0.4)
        if self.state[name]["running"] and self.is_healthy(name)[0]:
            return True, "already_running"
        cmd = info["start_cmd"]
        workdir = info["dir"]
        runtime = info.get("runtime") or (cmd[0] if cmd else None)
        run_cmd = [runtime, *cmd[1:]] if runtime and len(cmd) > 1 else ([runtime] if runtime else cmd)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            p = subprocess.Popen(
                run_cmd,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
            )
            self.state[name]["proc"] = p.pid
            self.state[name]["running"] = True
            self.state[name]["retries"] = 0
            self._save_state()
            for _ in range(40):
                if p.poll() is not None:
                    txt = self._read_output(p).strip()
                    return False, f"exited:{p.returncode}:{txt}"
                ok, detail = self.is_healthy(name)
                if ok:
                    return True, detail
                time.sleep(0.5)
            return True, f"started_but_not_healthy_yet:{p.pid}"
        except Exception as e:
            self.state[name]["running"] = False
            self._save_state()
            return False, repr(e)

    def stop(self, name):
        info = PROVIDERS[name]
        pid = self.state.get(name, {}).get("proc")
        if pid:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.kill(int(pid), 9)
            except Exception:
                pass
        port = info["port"]
        auth_ports = []
        if name == "deepseek":
            auth_ports = [9334]
        elif name == "qwen":
            auth_ports = [9335]
        for p in [port, *auth_ports]:
            self._kill_port(p)
        self.state[name]["running"] = False
        self.state[name].pop("proc", None)
        self._save_state()
        return True, "stopped"

    def re_auth(self, name):
        info = PROVIDERS.get(name)
        if not info:
            return False, "unknown_provider"
        re_auth = info.get("re_auth")
        if not re_auth:
            return False, "re_auth_not_supported"
        runtime = re_auth.get("runtime")
        cmd = re_auth.get("cmd", [])
        if not runtime or not cmd:
            return False, "re_auth_invalid"
        run_cmd = [runtime, *cmd[1:]] if runtime and len(cmd) > 1 else ([runtime] if runtime else cmd)
        env = dict(os.environ)
        env.update({k: str(v) for k, v in re_auth.get("env", {}).items()})
        try:
            completed = subprocess.run(
                run_cmd,
                cwd=info["dir"],
                capture_output=True,
                text=True,
                timeout=360,
                env=env,
                shell=False,
            )
            if completed.returncode == 0:
                return True, "re_auth_ok"
            return False, f"re_auth_failed:exit={completed.returncode}:{completed.stdout[:200]}:{completed.stderr[:200]}"
        except subprocess.TimeoutExpired:
            return False, "re_auth_timeout"
        except Exception as e:
            return False, repr(e)

    def get_status(self, name):
        ok, body = self.is_healthy(name)
        running = bool(self.state.get(name, {}).get("running"))
        return {
            "running": running,
            "healthy": ok,
            "detail": (body[:120] + "...") if isinstance(body, str) and len(body) > 120 else body,
            "alias": PROVIDERS[name]["alias"],
            "model": PROVIDERS[name]["default_model"],
        }

    def restart(self, name):
        self.stop(name)
        time.sleep(0.6)
        return self.start(name)

    def _read_output(self, p):
        if p.stdout:
            data = p.stdout.read()
            if data:
                try:
                    return data.decode("utf-8", errors="ignore")
                except Exception:
                    return repr(data)
        return ""

    def suggest_recovery(self, name):
        ok, body = self.is_healthy(name)
        if ok:
            return []
        txt = str(body).lower()
        if "token" in txt or "401" in txt or "403" in txt:
            return ["re_auth"]
        if "exited" in txt or "ECONNREFUSED" in txt or "404" in txt or "not found" in txt:
            return ["start"]
        return ["start", "re_auth"]
