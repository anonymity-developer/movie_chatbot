"""Exercise live chat and guide in local headless Chrome via CDP."""

from __future__ import annotations

import base64
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

from app.config import ROOT


CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
DEBUG_URL = "http://127.0.0.1:9223/json"


class Browser:
    def __init__(self, socket):
        self.socket = socket
        self.counter = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.counter += 1
        self.socket.send(json.dumps({"id": self.counter, "method": method, "params": params or {}}))
        while True:
            result = json.loads(self.socket.recv())
            if result.get("id") == self.counter:
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result.get("result", {})

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    def wait_for(self, expression: str, seconds: int = 45):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            time.sleep(0.25)
        raise TimeoutError(expression)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if not CHROME.exists():
        raise SystemExit("Chrome is not installed at the expected path")
    profile = ROOT / ".local" / f"chrome-smoke-{int(time.time())}"
    process = subprocess.Popen([
        str(CHROME), "--headless=new", "--remote-debugging-port=9223",
        "--remote-allow-origins=*", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tabs = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(DEBUG_URL, timeout=1) as response:
                    tabs = json.load(response)
                break
            except OSError:
                time.sleep(0.25)
        if tabs is None:
            raise RuntimeError("Chrome debugging port did not open")
        tab = next(item for item in tabs if item["type"] == "page")
        socket = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        try:
            browser = Browser(socket)
            browser.call("Page.enable")
            browser.call("Runtime.enable")
            browser.call("Page.navigate", {"url": args.base_url.rstrip('/') + '/'})
            cards = browser.wait_for("document.querySelectorAll('#movie-list button').length")
            browser.evaluate("document.querySelector('#movie-search').value = '인터스텔라'; document.querySelector('#movie-search-form').requestSubmit()")
            title_search_results = browser.wait_for("document.querySelector('#movie-search-status').textContent.includes('결과 1편') && document.querySelectorAll('#movie-list button').length === 1")
            browser.wait_for("document.querySelector('#selected-title').textContent === '인터스텔라' && !document.querySelector('#question').disabled")
            browser.evaluate("document.querySelector('#question').value = '인터스텔라의 과학 자문은?'; document.querySelector('#collect').click()")
            browser.wait_for("document.querySelector('#progress').textContent.includes('수집 완료')", seconds=150)
            browser.evaluate("document.querySelector('#question').value = '인터스텔라의 감독과 상영 시간은?'; document.querySelector('#chat-form').requestSubmit()")
            browser.wait_for("document.querySelectorAll('#messages article.assistant').length >= 1", seconds=150)
            chat_citations = browser.evaluate("document.querySelectorAll('#messages .citations li').length")
            browser.evaluate("document.querySelector('#guide').click()")
            browser.wait_for("document.querySelectorAll('#messages article.assistant').length >= 2", seconds=150)
            guide_citations = browser.evaluate("document.querySelectorAll('#messages .citations li').length")
            inline_links = browser.evaluate("document.querySelectorAll('#messages .inline-citation').length")
            default_citation_panels_closed = browser.evaluate("Array.from(document.querySelectorAll('#messages .evidence-panel')).every(panel => !panel.open)")
            desktop_columns = browser.evaluate("getComputedStyle(document.querySelector('.layout')).gridTemplateColumns")
            screenshot = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            (ROOT / "docs" / "temp" / "screenshots" / "live_chat_desktop.png").write_bytes(base64.b64decode(screenshot["data"]))
            browser.evaluate("document.querySelector('#messages .inline-citation').click()")
            opened_citation = browser.wait_for("document.querySelector('#messages .citations details[open]')?.id")
            citation_panel_open = browser.evaluate("document.querySelector('#messages .evidence-panel')?.open === true")
            screenshot = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            (ROOT / "docs" / "temp" / "screenshots" / "citation_open.png").write_bytes(base64.b64decode(screenshot["data"]))
            browser.evaluate("document.querySelectorAll('#messages .evidence-panel').forEach(panel => panel.open = false)")
            browser.call("Emulation.setDeviceMetricsOverride", {
                "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
            })
            time.sleep(0.5)
            mobile_columns = browser.evaluate("getComputedStyle(document.querySelector('.layout')).gridTemplateColumns")
            mobile_overflow = browser.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            mobile_library_hidden = browser.evaluate("getComputedStyle(document.querySelector('.movies')).display === 'none'")
            screenshot = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            (ROOT / "docs" / "temp" / "screenshots" / "live_chat.png").write_bytes(base64.b64decode(screenshot["data"]))
            browser.evaluate("document.querySelector('#toggle-library').click()")
            mobile_library_opens = browser.evaluate("getComputedStyle(document.querySelector('.movies')).display !== 'none'")
            browser.evaluate("document.querySelector('#toggle-library').click(); document.querySelector('#question').value = '저녁메뉴 추천좀'; document.querySelector('#chat-form').requestSubmit()")
            local_off_topic = browser.wait_for("Array.from(document.querySelectorAll('#messages article.assistant')).at(-1)?.textContent.includes('선택한 영화에 관한 질문')")
            browser.evaluate("document.querySelector('#question').value = '내가 방금 뭐 물어봤더라'; document.querySelector('#chat-form').requestSubmit()")
            local_recall = browser.wait_for("Array.from(document.querySelectorAll('#messages article.assistant')).at(-1)?.textContent.includes('저녁메뉴 추천좀')")
            browser.evaluate("window.originalFetch = window.fetch; window.fetch = (path, options) => path === '/api/chat' ? Promise.resolve(new Response(JSON.stringify({detail:{code:'MODEL_RATE_LIMITED',message:'AI 서비스가 요청을 제한했습니다.'}}), {status:503,headers:{'Content-Type':'application/json'}})) : window.originalFetch(path, options); document.querySelector('#question').value = '감독은 누구야?'; document.querySelector('#chat-form').requestSubmit()")
            rate_limit_preserves_question = browser.wait_for("document.querySelector('#question').value === '감독은 누구야?' && document.querySelector('#messages .chat-notice')?.textContent.includes('질문은 입력창에 남겨뒀어요')")
            browser.evaluate("window.fetch = window.originalFetch; delete window.originalFetch")
            report = {
                "movie_cards": cards, "title_search_results": title_search_results, "chat_citations": chat_citations,
                "total_citations_after_guide": guide_citations,
                "inline_citation_links": inline_links, "opened_citation": opened_citation,
                "default_citation_panels_closed": default_citation_panels_closed,
                "citation_panel_open": citation_panel_open,
                "desktop_columns": desktop_columns, "mobile_columns": mobile_columns,
                "mobile_horizontal_overflow": mobile_overflow,
                "mobile_library_hidden": mobile_library_hidden,
                "mobile_library_opens": mobile_library_opens,
                "local_off_topic": local_off_topic, "local_recall": local_recall,
                "rate_limit_preserves_question": rate_limit_preserves_question,
            }
            (ROOT / "docs" / "temp" / "브라우저검증.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report))
        finally:
            socket.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
