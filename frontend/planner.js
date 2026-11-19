const chat = document.querySelector('#chat');
const question = document.querySelector('#question');
const conversation = document.querySelector('#conversation');
const chatStatus = document.querySelector('#chat-status');
const planForm = document.querySelector('#save-plan');
const planList = document.querySelector('#plan-list');
const planStatus = document.querySelector('#plan-status');
const logList = document.querySelector('#log-list');
const logStatus = document.querySelector('#log-status');
const logDetail = document.querySelector('#log-detail');
const save = document.querySelector('#save');
const cancel = document.querySelector('#cancel');
let editing = null;

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
  const list = node('ol', '', 'timeline');
  for (const { event, data } of events) {
    let label;
    if (event === 'stage.enter') label = stageLabels[data.stage];
    if (event === 'replan') label = `재계획 ${data.attempt}회 · ${data.reason}`;
    if (event === 'tool.request') label = `${toolLabels[data.tool]} 조회 요청`;
    if (event === 'tool.response') {
      const outcome = data.result.ok ? '완료' : data.result.error.message;
      const movie = data.arguments.movie_id;
      label = `${toolLabels[data.tool]}${movie ? ` (${movie})` : ''} · ${outcome} · ${data.elapsed_ms}ms`;
    }
    if (event === 'run.error') label = data.message;
    if (event === 'worker.decision') label = `Worker ${data.action} · ${data.reason}`;
    if (event === 'review.result') label = `실행 평가 · ${reviews[data.status]} · 경고 ${data.warnings.length}건`;
    if (event === 'review.diagnosis') label = `원인 진단 · ${data.summary}`;
    if (label) list.append(node('li', label, event === 'replan' ? 'replan' : ''));
  }
  return list;
}

function fillPlan(plan) {
  editing = plan.id || null;
  for (const key of ['title', 'date', 'starts_at', 'ends_at']) planForm.elements[key].value = plan[key] || '';
  save.textContent = editing ? '수정 저장' : '계획 저장';
  cancel.hidden = !editing;
}

function renderTurn(message, result) {
  const article = node('article', '', 'turn');
  article.append(node('header', message));
  const mode = result.mode === 'demo' ? '데모 데이터 · ' : '';
  article.append(node('small', `${mode}${(result.elapsed_ms / 1000).toFixed(2)}초`));
  article.append(node('p', result.answer));
  for (const movie of result.recommendations) {
    const item = node('div');
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
    article.append(item);
  }
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
    const fallback = node('p');
    fallback.append(link);
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
    chatStatus.textContent = result.errors.map(error => error.message).filter((text, i, items) => items.indexOf(text) === i).join(' ');
    await refreshLogs();
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
      if (!window.confirm('이 관람 계획을 삭제할까요?')) return;
      try {
        await api(`/api/plans/${plan.id}`, 'DELETE');
        if (editing === plan.id) fillPlan({});
        planStatus.textContent = '계획을 삭제했습니다.';
        await refreshPlans();
      } catch (error) { planStatus.textContent = error.message; }
    }), action('캘린더 등록', async () => {
      try {
        const result = await api(`/api/plans/${plan.id}/calendar`, 'POST');
        planStatus.textContent = result.calendar_event
          ? `${result.calendar_event.source === 'google' ? 'Google Calendar' : '로컬 캘린더'}에 등록했습니다.`
          : result.answer;
        await refreshLogs();
      } catch (error) { planStatus.textContent = error.message; }
    }));
    item.append(actions);
    planList.append(item);
  }
}

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
  const runs = await api('/api/execution-logs');
  logList.replaceChildren();
  if (!runs.length) logList.append(node('li', '저장한 실행 기록이 없습니다.'));
  for (const run of runs) {
    const item = node('li');
    item.append(node('small', `${new Date(run.started_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} · 이벤트 ${run.event_count}개`));
    const actions = node('div', '', 'actions');
    actions.append(action('상세', async () => {
      try {
        logDetail.replaceChildren(timeline(await api(`/api/execution-logs/${run.run_id}`)));
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

// Establish the session cookie before the second request.
refreshPlans()
  .catch(error => { planStatus.textContent = error.message; })
  .then(refreshLogs)
  .catch(error => { logStatus.textContent = error.message; });
