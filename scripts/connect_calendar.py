"""Authorize this local agenttrip installation using a Google desktop OAuth client."""

import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from app.ai.planner_calendar import SCOPES
from app.config import ROOT


def main():
    client = ROOT / Path(os.getenv("GOOGLE_CALENDAR_CLIENT", ".local/google-calendar-client.json"))
    token = ROOT / Path(os.getenv("GOOGLE_CALENDAR_TOKEN", ".local/google-calendar-token.json"))
    if not client.is_file():
        raise SystemExit("Save the desktop OAuth JSON at GOOGLE_CALENDAR_CLIENT first.")
    if token.exists():
        raise SystemExit("This token file already exists. Choose a new local path to reconnect.")
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token.parent.mkdir(parents=True, exist_ok=True)
    with token.open("x", encoding="utf-8") as out:
        out.write(credentials.to_json())
    print("Google Calendar OAuth connected. Set PLANNER_CALENDAR_BACKEND=google and restart the app.")


if __name__ == "__main__":
    main()
