"""Open the local review UI without starting duplicate launcher servers."""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser

from . import config
from _scripts_2.path_contract import project_root

SERVER_PORT = 7893
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
REQUIRED_CAPABILITIES = frozenset({"audit_waiver", "audit_revert", "audit_skip_notes", "ai_log"})


def fetch_health(url):
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", url):
        return None
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            return json.load(response), response.status
    except urllib.error.HTTPError as exc:
        try:
            return json.load(exc), exc.code
        except (json.JSONDecodeError, ValueError):
            return None
    except (OSError, ValueError):
        return None


def is_ready(url):
    fetched = fetch_health(url)
    if not fetched:
        return False
    status, _code = fetched
    if status.get("app") != "exam-processor":
        return False
    if status.get("data_root") != str(config.DATA_ROOT.resolve()):
        return False
    if status.get("ready") is False:
        return False
    caps = set(status.get("capabilities") or [])
    return REQUIRED_CAPABILITIES.issubset(caps)


def _listener_pids(port):
    try:
        out = subprocess.check_output(
            ["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [int(line) for line in out.strip().splitlines() if line.strip().isdigit()]


def terminate_stale_server(url):
    """Stop an exam-processor listener that answers health but is not ready."""
    fetched = fetch_health(url)
    if not fetched:
        return False
    status, _code = fetched
    if status.get("app") != "exam-processor":
        return False
    if status.get("data_root") != str(config.DATA_ROOT.resolve()):
        return False
    if status.get("ready") is not False:
        return False
    stopped = False
    for pid in _listener_pids(SERVER_PORT):
        try:
            os.kill(pid, 15)
            stopped = True
        except OSError:
            continue
    return stopped


def server_environment():
    """Python source belongs to PROJECT; user content never supplies imports."""
    project = project_root()
    env = dict(os.environ)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project / '_scripts_2'}:{project}" + (f":{existing_pp}" if existing_pp else "")
    return project, env


def ensure_server():
    root = config.DATA_ROOT
    root.mkdir(parents=True, exist_ok=True)
    state = root / "launcher-url.txt"
    with (root / "launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_ready(SERVER_URL):
            state.write_text(SERVER_URL, encoding="utf-8")
            return SERVER_URL
        if terminate_stale_server(SERVER_URL):
            time.sleep(0.4)
            if is_ready(SERVER_URL):
                state.write_text(SERVER_URL, encoding="utf-8")
                return SERVER_URL
        # A file-backed log lets the child outlive this short launcher command.
        project, env = server_environment()
        with tempfile.NamedTemporaryFile(dir=root, prefix="server-", suffix=".log", delete=False) as log:
            process = subprocess.Popen(
                [sys.executable, "-m", __package__, "serve", "--port", str(SERVER_PORT)],
                cwd=project, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env=env,
            )
            log_path = log.name
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            with open(log_path) as stream:
                match = re.search(r"Exam Processor: (http://127\.0\.0\.1:\d+)", stream.read())
            if match and is_ready(match[1]):
                if match[1] != SERVER_URL:
                    raise RuntimeError(f"Exam Processor가 고정 주소가 아닌 곳에서 시작되었습니다: {match[1]}")
                state.write_text(SERVER_URL, encoding="utf-8")
                return SERVER_URL
            time.sleep(0.2)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise RuntimeError(
            f"Exam Processor를 {SERVER_URL}에서 시작하지 못했습니다. "
            f"다른 프로세스가 7893 포트를 사용 중인지 확인하세요. Log: {log_path}"
        )


def open_window(url):
    try:
        import webview
        from _scripts_2.apps.branding import apply_native_branding, program_meta

        meta = program_meta("exam-processor") or {}
        title = str(meta.get("name") or "Exam Processor")
        icon = apply_native_branding("exam-processor")
        window_kwargs = dict(
            title=title,
            url=url,
            width=1320,
            height=860,
            min_size=(960, 640),
            resizable=True,
            text_select=True,
            confirm_close=False,
        )
        if icon is not None:
            window_kwargs["icon"] = str(icon)
        try:
            webview.create_window(**window_kwargs)
        except TypeError:
            window_kwargs.pop("icon", None)
            webview.create_window(**window_kwargs)
        webview.start(debug=False)
        return True
    except Exception as exc:
        print(f"Native window unavailable ({exc}), opening in browser...", file=sys.stderr)
        return False


def main():
    url = ensure_server()
    if "--browser" in sys.argv or not open_window(url):
        if not webbrowser.open(url):
            raise RuntimeError(f"Open Exam Processor in a browser: {url}")
    print(url)


if __name__ == "__main__":
    main()
