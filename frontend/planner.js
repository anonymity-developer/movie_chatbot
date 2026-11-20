const chat = document.querySelector('#chat');
const question = document.querySelector('#question');
const conversation = document.querySelector('#conversation');
const chatStatus = document.querySelector('#chat-status');
const planForm = document.querySelector('#save-plan');
const planList = document.querySelector('#plan-list');
const planStatus = document.querySelector('#plan-status');
const calendarList = document.querySelector('#calendar-list');
const calendarStatus = document.querySelector('#calendar-status');
const calendarDays = document.querySelector('#calendar-days');
const calendarMonthLabel = document.querySelector('#calendar-month');
const calendarSelection = document.querySelector('#calendar-selection');
const logs = document.querySelector('#logs');
const logList = document.querySelector('#log-list');
const logStatus = document.querySelector('#log-status');
const logDetail = document.querySelector('#log-detail');
const save = document.querySelector('#save');
const cancel = document.querySelector('#cancel');
let editing = null;
const seoulDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' });
function todayKey() {
  const parts = Object.fromEntries(seoulDate.formatToParts(new Date()).map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month.padStart(2, '0')}-${parts.day.padStart(2, '0')}`;
}
let selectedDate = todayKey();
let visibleMonth = selectedDate.slice(0, 7);
let calendarEntries = [];

const stageLabels = {
  analyze: '요청 분석', plan: 'Tool 계획', execute: 'Tool 실행',
  judge: '시간 조건 확인', replan: '재계획', respond: '응답 생성',
};
const toolLabels = {
  box_office: '일별 박스오피스', weekly_box_office: '주간 박스오피스', movie_detail: '영화 상세',
  theaters: '영화관 위치', calendar_busy: '겹치는 일정 확인', calendar_register: '캘린더 등록',
  rag_search: '작품 근거 검색',
};
const reviews = { normal: '이상 없음', abnormal: '이상 징후 발견', undetermined: '판정 불가' };
const runLabels = {
  recommended: '추천 완료', no_match: '조건에 맞는 후보 없음', answered: '답변 완료',
  clarify: '추가 조건 필요', unavailable: '연결 실패',
};

function node(tag, content, className) {
  const element = document.createElement(tag);
  if (content) element.textContent = content;
  if (className) element.className = className;
  return element;
}

function action(label, callback) {
  const button = node('button', label, 'secondary');
  button.type = 'button';
  button.addEventListener('click', callback);
  return button;
}

async function api(path, method = 'GET', data) {
  const response = await fetch(path, {
    method,
    ...(data ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}),
  });
  if (response.status === 204) return null;
  if (!response.ok) {
    if (response.status === 422) throw new Error('입력한 값과 시작·종료 시각을 확인해 주세요.');
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.message || '요청을 처리하지 못했습니다.');
  }
  return response.json();
}

function timeline(events) {
  const view = node('div', '', 'execution-view');
  const requests = events.filter(item => item.event === 'tool.request').length;
  const failures = events.filter(item => item.event === 'tool.response' && !item.data.result.ok).length;
  const replans = events.filter(item => item.event === 'replan').length;
  view.append(node('p', `Tool 호출 ${requests}회 · 실패 ${failures}회 · 재계획 ${replans}회`, 'execution-counts'));
  const list = node('ol', '', 'timeline');
  for (const { event, data } of events) {
    let label;
    let description;
    let tag;
    let tone;
    const tool = toolLabels[data.tool] || data.tool;
    if (event === 'stage.enter' && data.stage !== 'replan') {
      label = stageLabels[data.stage] || data.stage;
      tone = 'stage';
    }
    if (event === 'analysis.decision' && data.reason) {
      label = '요청 분석 근거';
      description = data.reason;
    }
    if (event === 'replan') {
      label = `재계획 ${data.attempt}회`;
      description = data.reason;
      tone = 'replan';
    }
    if (event === 'tool.response') {
      const subject = data.result.data?.title || data.arguments?.region || data.arguments?.movie_id;
      label = subject ? `${tool} · ${subject}` : tool;
      tag = `${data.result.ok ? '완료' : '실패'} · ${formatDuration(data.elapsed_ms)}`;
      tone = data.result.ok ? 'tool-result' : 'failure';
      description = data.result.error?.message;
    }
    if (event === 'constraints.checked') {
      label = '시간 조건 판정';
      description = `적합 ${data.matching}편 · 제외 ${data.excluded}편`;
    }
    if (event === 'run.error') {
      label = '실행 오류';
      description = data.message;
      tone = 'failure';
    }
    if (event === 'worker.decision') {
      const actions = { retry: '재시도', skip: '건너뜀', replan: '재계획 요청' };
      label = `${tool} · ${actions[data.action] || data.action}`;
      description = data.reason;
    }
    if (event === 'review.result') {
      label = '실행 평가';
      description = `${reviews[data.status] || data.status} · 경고 ${data.warnings.length}건`;
    }
    if (event === 'review.diagnosis') {
      label = '원인 진단';
      description = data.summary;
    }
    if (!label) continue;
    const entry = node('li', '', tone);
    const heading = node('div', '', 'timeline-heading');
    heading.append(node('strong', label));
    if (tag) heading.append(node('span', tag, 'result-tag'));
    entry.append(heading);
    if (description) entry.append(node('p', description));
    list.append(entry);
  }
  view.append(list);
  return view;
}

function formatDuration(ms) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}초`;
}

function fillPlan(plan) {
  editing = plan.id || null;
  for (const key of ['title', 'date', 'starts_at', 'ends_at']) planForm.elements[key].value = plan[key] || '';
  save.textContent = editing ? '수정 저장' : '계획 저장';
  cancel.hidden = !editing;
}

function renderTurn(message, result) {
  const article = node('article', '', 'turn');
  const header = node('header', '', 'turn-header');
  const meta = node('div', '', 'turn-meta');
  const condition = result.status === 'unavailable' ? 'failure' : '';
  if (runLabels[result.status]) meta.append(node('span', runLabels[result.status], `result-tag ${condition}`));
  const mode = result.mode === 'demo' ? '데모 데이터 · ' : '';
  meta.append(node('small', `${mode}${formatDuration(result.elapsed_ms)}`));
  header.append(node('h3', message), meta);
  article.append(header);
  article.append(node('p', result.answer));
  const movieCards = node('div', '', 'movie-cards');
  for (const movie of result.recommendations) {
    const item = node('div', '', 'movie-card');
    item.append(
      node('strong', movie.title),
      node('p', `러닝타임 ${movie.runtime}분 · 늦어도 ${movie.latest_start} 시작${movie.rating ? ` · ${movie.rating}` : ''}`),
      action('계획에 담기', () => {
        fillPlan({ title: movie.title, date: result.conditions.date, starts_at: movie.latest_start,
          ends_at: movie.latest_end || result.conditions.deadline.slice(0, 5) });
        planStatus.textContent = '실제 상영 시간을 확인한 뒤 저장하세요.';
        planForm.elements.starts_at.focus();
      }),
    );
    movieCards.append(item);
  }
  if (movieCards.childElementCount) article.append(movieCards);
  if (result.documents?.length) {
    article.append(node('h3', '근거 문서'));
    const list = node('ul');
    for (const documentRef of result.documents) {
      const item = node('li');
      if (/^https?:\/\//i.test(documentRef.url_or_file)) {
        const link = node('a', documentRef.title);
        link.href = documentRef.url_or_file;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(link);
      } else {
        item.append(node('strong', documentRef.title));
      }
      item.append(node('p', documentRef.excerpt));
      if (documentRef.license) item.append(node('small', documentRef.license));
      list.append(item);
    }
    article.append(list);
  }
  if (result.theaters.length) {
    article.append(node('h3', '주변 영화관'));
    const list = node('ul');
    for (const theater of result.theaters) {
      const item = node('li', `${theater.name}${theater.address ? ` · ${theater.address}` : ''}`);
      if (/^https?:\/\//i.test(theater.url)) {
        const link = node('a', ' 위치 보기');
        link.href = theater.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(link);
      }
      list.append(item);
    }
    article.append(list);
    if (result.mode === 'live') {
      const source = node('a', '© OpenStreetMap contributors');
      source.href = 'https://www.openstreetmap.org/copyright';
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      article.append(source);
    }
  } else if (result.mode === 'live' && result.conditions.region && result.errors.some(error => error.tool === 'theaters')) {
    const link = node('a', '지도에서 영화관 찾기');
    link.href = `https://www.openstreetmap.org/search?query=${encodeURIComponent(result.conditions.region + ' 영화관')}`;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    const fallback = node('div', '', 'theater-fallback');
    fallback.append(node('p', '영화관 위치를 가져오지 못했습니다.'), link);
    article.append(fallback);
  }
  if (result.evidence.length || result.excluded.length) {
    const details = node('details');
    details.append(node('summary', '후보 조회 기준과 제외 사유'));
    for (const evidence of result.evidence) {
      const requested = evidence.requested_date === evidence.lookup_date ? '' : ` · 요청 관람일 ${evidence.requested_date}`;
      const period = evidence.period === 'weekly' ? '주간 기준일' : '일별 집계일';
      details.append(node('p', `${evidence.source} ${period} ${evidence.lookup_date}${requested}`));
    }
    for (const movie of result.excluded) details.append(node('p', `${movie.title} · ${movie.reason}`));
    article.append(details);
  }
  const details = node('details');
  details.append(node('summary', result.replans ? `실행 과정 · 재계획 ${result.replans}회` : '실행 과정'), timeline(result.events));
  article.append(details);
  if (!result.log_saved) article.append(node('p', '실행 기록 저장에 실패했습니다.', 'notice'));
  conversation.prepend(article);
}

document.querySelector('#example').addEventListener('click', () => {
  question.value = question.placeholder;
  question.focus();
});

chat.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = question.value.trim();
  if (!message) return;
  const send = document.querySelector('#send');
  send.disabled = true;
  chatStatus.textContent = '영화와 주변 영화관을 확인하고 있어요…';
  try {
    const result = await api('/api/planner/chat', 'POST', { message });
    renderTurn(message, result);
    if (question.value.trim() === message) question.value = '';
    chatStatus.textContent = '';
    await refreshLogs();
    if (result.calendar_event?.source === 'local') await refreshCalendar(result.calendar_event.event_id);
  } catch (error) {
    chatStatus.textContent = error.message;
  } finally {
    send.disabled = false;
  }
});

async function refreshPlans() {
  const plans = await api('/api/plans');
  planList.replaceChildren();
  if (!plans.length) planList.append(node('li', '저장한 관람 계획이 없습니다.'));
  for (const plan of plans) {
    const item = node('li');
    item.append(node('strong', plan.title), node('p', `${plan.date} · ${plan.starts_at}–${plan.ends_at}`));
    const actions = node('div', '', 'actions');
    actions.append(action('수정', async () => {
      try {
        fillPlan(await api(`/api/plans/${plan.id}`));
        planForm.elements.title.focus();
      } catch (error) { planStatus.textContent = error.message; }
    }), action('삭제', async () => {
      if (!window.confirm('이 관람 계획과 연결된 로컬 캘린더 일정을 삭제할까요?')) return;
      try {
        await api(`/api/plans/${plan.id}`, 'DELETE');
        if (editing === plan.id) fillPlan({});
        planStatus.textContent = '계획을 삭제했습니다.';
        await Promise.all([refreshPlans(), refreshCalendar()]);
      } catch (error) { planStatus.textContent = error.message; }
    }), action('캘린더 등록', async () => {
      try {
        const result = await api(`/api/plans/${plan.id}/calendar`, 'POST');
        planStatus.textContent = result.calendar_event
          ? `${result.calendar_event.source === 'google' ? 'Google Calendar' : '로컬 캘린더'}에 등록했습니다.`
          : result.answer;
        await Promise.all([refreshLogs(), refreshCalendar(result.calendar_event?.event_id)]);
      } catch (error) { planStatus.textContent = error.message; }
    }));
    item.append(actions);
    planList.append(item);
  }
}

function renderCalendar() {
  const [year, month] = visibleMonth.split('-').map(Number);
  const firstWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const dayCount = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const today = todayKey();
  calendarMonthLabel.textContent = `${year}년 ${month}월`;
  calendarDays.replaceChildren();
  for (let blank = 0; blank < firstWeekday; blank++) {
    const spacer = node('span');
    spacer.setAttribute('aria-hidden', 'true');
    calendarDays.append(spacer);
  }
  for (let day = 1; day <= dayCount; day++) {
    const date = `${visibleMonth}-${String(day).padStart(2, '0')}`;
    const entries = calendarEntries.filter(entry => entry.date === date);
    const cell = action('', () => {
      selectedDate = date;
      calendarStatus.textContent = '';
      renderCalendar();
      calendarDays.querySelector('[aria-pressed="true"]').focus();
    });
    cell.className = 'calendar-day';
    cell.setAttribute('aria-label', `${year}년 ${month}월 ${day}일${entries.length ? `, 일정 ${entries.length}건` : ''}`);
    cell.setAttribute('aria-pressed', String(date === selectedDate));
    if (date === today) cell.setAttribute('aria-current', 'date');
    cell.append(node('span', String(day), 'day-number'));
    if (entries.length) {
      const label = entries.length === 1 ? entries[0].title : `일정 ${entries.length}건`;
      cell.append(node('span', label, 'day-event'));
    }
    calendarDays.append(cell);
  }
  renderCalendarSelection();
}

function renderCalendarSelection() {
  const entries = calendarEntries.filter(entry => entry.date === selectedDate);
  const [year, month, day] = selectedDate.split('-').map(Number);
  calendarSelection.textContent = `${year}년 ${month}월 ${day}일 일정`;
  calendarList.replaceChildren();
  if (!entries.length) calendarList.append(node('li', '이날 등록된 일정이 없습니다.', 'empty-day'));
  for (const entry of entries) {
    const item = node('li');
    const actions = node('div', '', 'actions');
    actions.append(action('일정 삭제', async () => {
      if (!window.confirm('이 로컬 캘린더 일정을 삭제할까요? 관람 계획은 유지됩니다.')) return;
      try {
        await api(`/api/calendar/local/${entry.id}`, 'DELETE');
        await refreshCalendar();
        calendarStatus.textContent = '일정을 삭제했습니다. 새로 검색하면 변경된 일정이 반영됩니다.';
      } catch (error) { calendarStatus.textContent = error.message; }
    }));
    item.append(node('strong', entry.title), node('p', `${entry.starts_at}–${entry.ends_at}`), actions);
    calendarList.append(item);
  }
}

async function refreshCalendar(focusEventId) {
  calendarEntries = await api('/api/calendar/local');
  const entry = focusEventId && calendarEntries.find(item => item.id === focusEventId);
  if (entry) {
    selectedDate = entry.date;
    visibleMonth = selectedDate.slice(0, 7);
  }
  renderCalendar();
}

function changeCalendarMonth(offset) {
  const [year, month] = visibleMonth.split('-').map(Number);
  const today = todayKey();
  visibleMonth = offset ? new Date(Date.UTC(year, month - 1 + offset, 1)).toISOString().slice(0, 7) : today.slice(0, 7);
  selectedDate = visibleMonth === today.slice(0, 7) ? today : `${visibleMonth}-01`;
  calendarStatus.textContent = '';
  renderCalendar();
}

document.querySelector('#calendar-prev').addEventListener('click', () => changeCalendarMonth(-1));
document.querySelector('#calendar-today').addEventListener('click', () => changeCalendarMonth(0));
document.querySelector('#calendar-next').addEventListener('click', () => changeCalendarMonth(1));

cancel.addEventListener('click', () => fillPlan({}));
planForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  save.disabled = true;
  try {
    const path = editing ? `/api/plans/${editing}` : '/api/plans';
    const result = await api(path, editing ? 'PUT' : 'POST', Object.fromEntries(new FormData(planForm)));
    const conflicts = result.overlaps.map(plan => plan.title).join(', ');
    planStatus.textContent = conflicts ? `저장했습니다. 시간이 겹치는 계획 · ${conflicts}` : '계획을 저장했습니다.';
    fillPlan({});
    await refreshPlans();
  } catch (error) {
    planStatus.textContent = error.message;
  } finally {
    save.disabled = false;
  }
});

async function refreshLogs() {
  if (!logs.open) return;
  const runs = await api('/api/execution-logs');
  logList.replaceChildren();
  if (!runs.length) logList.append(node('li', '저장한 실행 기록이 없습니다.'));
  for (const run of runs) {
    const item = node('li');
    item.append(node('small', `${new Date(run.started_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} · 이벤트 ${run.event_count}개`));
    const actions = node('div', '', 'actions');
    actions.append(action('상세', async () => {
      try {
        const events = await api(`/api/execution-logs/${run.run_id}`);
        const end = events.find(item => item.event === 'run.end')?.data;
        const date = new Date(run.started_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
        const title = end && runLabels[end.status] ? runLabels[end.status] : '실행 상세';
        logDetail.replaceChildren(node('h3', title), node('small', date), timeline(events));
        logStatus.textContent = '';
      } catch (error) { logStatus.textContent = error.message; }
    }), action('삭제', async () => {
      if (!window.confirm('이 실행 로그를 삭제할까요?')) return;
      try {
        await api(`/api/execution-logs/${run.run_id}`, 'DELETE');
        logDetail.replaceChildren();
        await refreshLogs();
      } catch (error) { logStatus.textContent = error.message; }
    }));
    item.append(actions);
    logList.append(item);
  }
}

document.querySelector('nav a[href="#logs"]').addEventListener('click', () => { logs.open = true; });
logs.addEventListener('toggle', () => {
  refreshLogs().catch(error => { logStatus.textContent = error.message; });
});

// Establish the session cookie before the second request.
refreshPlans()
  .catch(error => { planStatus.textContent = error.message; })
  .then(() => Promise.all([
    refreshLogs().catch(error => { logStatus.textContent = error.message; }),
    refreshCalendar().catch(error => { calendarStatus.textContent = error.message; }),
  ]));
