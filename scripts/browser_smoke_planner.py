"""Verify the planner in headless Chrome and capture a submission screenshot."""

import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

import websocket

from scripts.browser_smoke import Browser, CHROME


def wait_json(url):
    for _ in range(80):
        try:
            with urlopen(url, timeout=1) as response:
                return json.load(response)
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Service failed to start at {url}")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chrome", type=Path, default=CHROME)
    args = parser.parse_args()
    app_port, cdp_port = free_port(), free_port()
    base = f"http://127.0.0.1:{app_port}"
    with TemporaryDirectory() as directory:
        env = {**os.environ, "PLANNER_DEMO": "true", "MOVIELAB_CACHE_DB": str(Path(directory) / "browser.sqlite")}
        server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "app.api.main:app", "--port", str(app_port)], env=env)
        chrome = None
        try:
            wait_json(base + "/openapi.json")
            chrome = subprocess.Popen([
                str(args.chrome), "--headless=new", f"--remote-debugging-port={cdp_port}",
                f"--user-data-dir={Path(directory) / 'chrome'}", "--window-size=1280,1050", "about:blank",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tabs = wait_json(f"http://127.0.0.1:{cdp_port}/json")
            target = next(item for item in tabs if item["type"] == "page")
            connection = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
            try:
                browser = Browser(connection)
                browser.call("Page.enable")
                browser.call("Page.navigate", {"url": base + "/planner"})
                browser.wait_for("document.querySelector('#log-list')?.children.length")
                browser.evaluate("""
                    document.querySelector('#question').value = '내일 15:00 이후 강남에서 보고 16:40 전에 끝나는 영화';
                    document.querySelector('#chat').requestSubmit();
                """)
                browser.wait_for("document.querySelector('#conversation')?.textContent.includes('재계획 1회')")
                browser.evaluate("Array.from(document.querySelectorAll('#conversation button')).find(b => b.textContent === '계획에 담기').click()")
                browser.evaluate("document.querySelector('#save-plan').requestSubmit()")
                browser.wait_for("document.querySelector('#plan-list')?.textContent.includes('짧은 산책')")
                browser.evaluate("document.querySelector('#plan-list button').click()")
                browser.wait_for("document.querySelector('#save')?.textContent === '수정 저장'")
                browser.evaluate("document.querySelector('#save-plan [name=title]').value += ' (수정)'; document.querySelector('#save-plan').requestSubmit()")
                browser.wait_for("document.querySelector('#plan-list')?.textContent.includes('(수정)')")
                browser.evaluate("document.querySelector('#conversation details:last-of-type').open = true; window.scrollTo(0, 0)")
                capture = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
                args.output.write_bytes(base64.b64decode(capture["data"]))
                browser.evaluate("document.querySelectorAll('#plan-list button')[2].click()")
                browser.wait_for("document.querySelector('#plan-status')?.textContent.includes('로컬 캘린더에 등록했습니다')")
                browser.wait_for("document.querySelectorAll('#log-list li').length === 2")
                browser.evaluate("window.confirm = () => true; document.querySelectorAll('#plan-list button')[1].click()")
                browser.wait_for("document.querySelector('#plan-list')?.textContent.includes('저장한 관람 계획이 없습니다')")
                browser.evaluate("document.querySelectorAll('#log-list li')[1].querySelector('button').click()")
                browser.wait_for("Boolean(document.querySelector('#log-detail .replan'))")
                browser.call("Page.navigate", {"url": base + "/docs"})
                browser.wait_for("document.querySelector('.swagger-ui')?.textContent.includes('/api/planner/chat')")
                browser.evaluate("document.querySelector('[id*=movie-planner]')?.scrollIntoView()")
                screenshot = browser.call("Page.captureScreenshot", {"format": "png"})
                args.output.with_name("api-docs.png").write_bytes(base64.b64decode(screenshot["data"]))
            finally:
                connection.close()
        finally:
            for process in (chrome, server):
                if process is not None:
                    process.terminate()
                    process.wait(timeout=10)
    print("Browser passed: replan, plan save/edit/delete, local calendar registration, log detail, OpenAPI.")


if __name__ == "__main__":
    main()
