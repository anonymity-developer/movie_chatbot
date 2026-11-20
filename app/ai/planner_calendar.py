"""Same calendar tool contracts over local SQLite and Google Calendar."""

from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID

from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from httplib2 import Http, HttpLib2Error
from pydantic import BaseModel

from app.ai.planner_tools import Tool, ToolFailure


SEOUL = timezone(timedelta(hours=9))
SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.freebusy"]


class CalendarDay(BaseModel):
    date: date


class Registration(BaseModel):
    plan_id: UUID


class BusySlot(BaseModel):
    starts_at: datetime
    ends_at: datetime


class CalendarAvailability(BaseModel):
    source: str
    busy: list[BusySlot]


class CalendarEvent(BaseModel):
    source: str
    event_id: str


class LocalCalendar:
    source = "local"

    def __init__(self, repository, session_id):
        self.repository = repository
        self.session_id = session_id

    def busy(self, day):
        return [
            {field: datetime.combine(day, time.fromisoformat(value), SEOUL) for field, value in row.items()}
            for row in self.repository.calendar_intervals(self.session_id, day.isoformat())
        ]

    def register(self, plan):
        if not self.repository.register_calendar_plan(self.session_id, plan):
            raise ToolFailure("NOT_FOUND", "저장한 관람 계획을 찾을 수 없습니다.")
        return plan["id"]


class GoogleCalendar:
    source = "google"

    def __init__(self, token, calendar_id):
        self.token = token
        self.calendar_id = calendar_id

    def service(self):
        if not self.token.is_file():
            raise ToolFailure("MISSING_KEY", "Google Calendar OAuth 연결을 완료해 주세요.")
        credentials = Credentials.from_authorized_user_file(str(self.token), SCOPES)
        return build("calendar", "v3", http=AuthorizedHttp(credentials, http=Http(timeout=6)), cache_discovery=False)

    def busy(self, day):
        start = datetime.combine(day, time.min, SEOUL)
        end = start + timedelta(days=1)
        with self.service() as service:
            response = service.freebusy().query(body={
                "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": self.calendar_id}],
            }).execute()
        calendar = response.get("calendars", {}).get(self.calendar_id)
        if not calendar or calendar.get("errors"):
            raise ToolFailure("SOURCE_UNAVAILABLE", "Google Calendar에서 일정 가능 시간을 확인하지 못했습니다.")
        return [{"starts_at": item["start"], "ends_at": item["end"]} for item in calendar.get("busy", [])]

    def register(self, plan):
        event_id = UUID(plan["id"]).hex
        body = {
            "id": event_id, "summary": plan["title"],
            "start": {"dateTime": f"{plan['date']}T{plan['starts_at']}:00+09:00"},
            "end": {"dateTime": f"{plan['date']}T{plan['ends_at']}:00+09:00"},
        }
        with self.service() as service:
            events = service.events()
            try:
                events.insert(calendarId=self.calendar_id, body=body).execute()
            except HttpError as exc:
                if exc.resp.status != 409:
                    raise
                events.patch(calendarId=self.calendar_id, eventId=event_id,
                             body={key: value for key, value in body.items() if key != "id"}).execute()
        return event_id


def calendar_tools(repository, session_id, *, demo, loopback):
    mode = os.getenv("PLANNER_CALENDAR_BACKEND", "local").lower()
    calendar = (GoogleCalendar(Path(os.getenv("GOOGLE_CALENDAR_TOKEN", ".local/google-calendar-token.json")),
                               os.getenv("GOOGLE_CALENDAR_ID", "primary"))
                if not demo and mode == "google" else LocalCalendar(repository, session_id))

    def checked(operation, value):
        if calendar.source == "google" and not loopback:
            raise ToolFailure("FORBIDDEN", "Google Calendar는 이 기기의 로컬 접속에서 연결할 수 있습니다.")
        try:
            return operation(value)
        except (HttpError, HttpLib2Error, GoogleAuthError, OSError, ValueError, KeyError):
            raise ToolFailure("SOURCE_UNAVAILABLE", "캘린더 서비스를 사용할 수 없습니다. 연결 상태를 확인해 주세요.") from None

    def lookup(args):
        return {"source": calendar.source, "busy": checked(calendar.busy, args.date)}

    def register(args):
        plan = repository.plan(session_id, str(args.plan_id))
        if not plan:
            raise ToolFailure("NOT_FOUND", "저장한 관람 계획을 찾을 수 없습니다.")
        return {"source": calendar.source, "event_id": checked(calendar.register, plan)}

    return {
        "calendar_busy": Tool("관람일에 겹치는 일정을 확인할 때", CalendarDay, CalendarAvailability, lookup, "local"),
        "calendar_register": Tool("사용자가 저장한 계획 ID의 등록을 요청할 때", Registration, CalendarEvent, register, "local"),
    }
