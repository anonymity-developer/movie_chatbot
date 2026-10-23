const movieList = document.querySelector('#movie-list');
const search = document.querySelector('#movie-search');
const searchForm = document.querySelector('#movie-search-form');
const searchStatus = document.querySelector('#movie-search-status');
const more = document.querySelector('#more-movies');
const title = document.querySelector('#selected-title');
const description = document.querySelector('#selected-description');
const messages = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const question = document.querySelector('#question');
const send = document.querySelector('#send');
const collect = document.querySelector('#collect');
const guide = document.querySelector('#guide');
const progress = document.querySelector('#progress');
const discoverForm = document.querySelector('#discover-form');
const discoverCondition = document.querySelector('#discover-condition');
const discoverResults = document.querySelector('#discover-results');
const discoverStatus = document.querySelector('#discover-status');
const discoverSubmit = document.querySelector('#discover-submit');
const toggleLibrary = document.querySelector('#toggle-library');
let selectedMovieId = null;
let conversationId = null;
let selectionSequence = 0;
let page = 1;
let searchTimer;
let waitTimer;
let citationSequence = 0;
let movieLoadSequence = 0;

const sourceNames = { movie_metadata: '영화 정보', wikipedia: '위키백과', wikidata: '위키데이터' };

function startWaiting(message) {
  const started = performance.now();
  clearInterval(waitTimer);
  const update = () => { progress.textContent = `${message} · ${Math.floor((performance.now() - started) / 1000)}초 경과`; };
  update();
  waitTimer = setInterval(update, 1000);
}

function stopWaiting() { clearInterval(waitTimer); }

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.detail?.message || '요청을 처리하지 못했습니다.');
    error.code = body.detail?.code;
    throw error;
  }
  return body;
}

function addMessage(role, content, citations = []) {
  const article = document.createElement('article');
  const group = role === 'assistant' ? ++citationSequence : 0;
  article.className = `message ${role}`;
  const label = document.createElement('strong');
  label.textContent = role === 'user' ? '나' : 'Movie Lab';
  const text = document.createElement('div');
  text.className = 'message-body';
  for (const paragraph of content.split(/\n\s*\n/)) {
    if (!paragraph.trim()) continue;
    const block = document.createElement('p');
    const parts = paragraph.trim().split(/(\[근거\d+\])/g);
    for (const part of parts) {
      const id = part.match(/^\[(근거\d+)\]$/)?.[1];
      const citation = id && citations.find(item => item.citation_id === id);
      if (citation) {
        const link = document.createElement('a');
        link.className = 'inline-citation';
        link.href = `#source-${group}-${id}`;
        link.textContent = id.replace('근거', '');
        link.setAttribute('aria-label', `${id}: ${citation.title}`);
        link.addEventListener('click', event => {
          event.preventDefault();
          const card = article.querySelector(`#source-${group}-${id}`);
          if (card) {
            article.querySelector('.evidence-panel').open = true;
            card.open = true;
            card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
        });
        block.append(link);
      } else {
        block.append(document.createTextNode(part));
      }
    }
    text.append(block);
  }
  article.append(label, text);
  if (role === 'assistant') {
    showCitations(citations, article, group);
    if (!citations.length && content === '제공된 문서에서 확인되지 않습니다.') {
      const note = document.createElement('small');
      note.className = 'no-citations';
      note.textContent = '인용된 근거 문서가 없습니다.';
      article.append(note);
    }
  }
  messages.append(article);
  messages.scrollTop = role === 'assistant'
    ? Math.max(0, article.offsetTop - messages.offsetTop - 12)
    : messages.scrollHeight;
  return article;
}

function showCitations(citations, article, group) {
  if (!citations.length) return;
  const panel = document.createElement('details');
  panel.className = 'evidence-panel';
  const heading = document.createElement('summary');
  heading.textContent = `출처 ${citations.length}개 확인`;
  const list = document.createElement('ol');
  list.className = 'citations';
  for (const citation of citations) {
    const item = document.createElement('li');
    const details = document.createElement('details');
    details.id = `source-${group}-${citation.citation_id}`;
    const summary = document.createElement('summary');
    const number = document.createElement('span');
    number.className = 'source-number';
    number.textContent = citation.citation_id.replace('근거', '');
    const source = document.createElement('span');
    source.className = 'source-label';
    source.textContent = sourceNames[citation.source_type] || citation.source_type;
    const name = document.createElement('span');
    name.className = 'source-title';
    name.textContent = citation.title;
    summary.append(number, name, source);
    details.append(summary);
    if (citation.excerpt) {
      const excerptLabel = document.createElement('small');
      excerptLabel.className = 'excerpt-label';
      excerptLabel.textContent = '답변과 관련된 원문 부분';
      details.append(excerptLabel);
      const excerpt = document.createElement('p');
      excerpt.className = 'citation-excerpt';
      excerpt.textContent = citation.excerpt;
      details.append(excerpt);
    }
    if (citation.license) {
      const license = document.createElement('small');
      license.textContent = `이용 조건: ${citation.license} `;
      details.append(license);
    }
    if (citation.url_or_file) {
      try {
        const url = new URL(citation.url_or_file);
        if (url.protocol === 'https:') {
          const link = document.createElement('a');
          link.href = url.href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = '원문 열기 ↗';
          details.append(link);
        }
      } catch { /* Local file sources have no public link. */ }
    }
    item.append(details);
    list.append(item);
  }
  panel.append(heading, list);
  article.append(panel);
}

function addNotice(message) {
  const note = document.createElement('p');
  note.className = 'chat-notice';
  note.setAttribute('role', 'alert');
  note.textContent = message;
  messages.append(note);
  note.scrollIntoView({ block: 'nearest' });
}

async function loadMovies(reset = false) {
  const requestId = ++movieLoadSequence;
  if (reset) { page = 1; movieList.replaceChildren(); }
  const data = await api(`/api/movies?query=${encodeURIComponent(search.value)}&page=${page}`);
  if (requestId !== movieLoadSequence) return null;
  for (const movie of data.items) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${movie.title} (${movie.year || '연도 미상'})`;
    button.dataset.movieId = movie.movie_id;
    button.setAttribute('aria-current', String(movie.movie_id === selectedMovieId));
    button.addEventListener('click', () => selectMovie(movie.movie_id));
    item.append(button);
    movieList.append(item);
  }
  more.hidden = page * 24 >= data.total;
  searchStatus.textContent = search.value.trim()
    ? `제목 검색 결과 ${data.total}편${data.total === 0 ? ' · 다른 제목이나 원제를 입력해 보세요.' : ''}`
    : `전체 영화 ${data.total}편 · 제목만 입력해 검색하세요.`;
  return data;
}

async function selectMovie(movieId) {
  const sequence = ++selectionSequence;
  try {
    const movie = await api(`/api/movies/${encodeURIComponent(movieId)}`);
    if (sequence !== selectionSequence) return;
    selectedMovieId = movieId;
    document.body.classList.add('movie-selected');
    document.body.classList.remove('library-open');
    toggleLibrary.hidden = false;
    conversationId = null;
    title.textContent = movie.title;
    description.textContent = `${movie.year || '연도 미상'} · ${(movie.genres || []).join(', ')}${movie.runtime ? ` · ${movie.runtime}분` : ''}${movie.director?.length ? ` · 감독 ${movie.director.join(', ')}` : ''}`;
    messages.replaceChildren();
    question.disabled = true;
    send.disabled = true;
    collect.disabled = true;
    guide.disabled = true;
    progress.textContent = '이 영화의 대화 기록을 불러오는 중…';
    const history = await api(`/api/conversations/${encodeURIComponent(movieId)}`);
    if (sequence !== selectionSequence) return;
    conversationId = history.conversation_id;
    for (const message of history.messages) {
      addMessage(message.role, message.content, message.citations || []);
    }
    question.disabled = false;
    send.disabled = false;
    collect.disabled = false;
    guide.disabled = false;
    progress.textContent = '';
    document.querySelectorAll('[data-movie-id]').forEach(button => {
      button.setAttribute('aria-current', String(button.dataset.movieId === movieId));
    });
    question.focus();
  } catch (error) { if (sequence === selectionSequence) progress.textContent = error.message; }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!selectedMovieId || !question.value.trim()) return;
  const movieId = selectedMovieId;
  const selection = selectionSequence;
  const prompt = question.value.trim();
  question.value = '';
  messages.querySelectorAll('.chat-notice').forEach(note => note.remove());
  const pendingMessage = addMessage('user', prompt);
  question.disabled = true;
  send.disabled = true;
  collect.disabled = true;
  guide.disabled = true;
  startWaiting('근거를 검색하고 답변을 생성하는 중');
  try {
    const result = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: movieId, question: prompt, conversation_id: conversationId }),
    });
    if (selectedMovieId === movieId && selection === selectionSequence) {
      conversationId = result.conversation_id;
      addMessage('assistant', result.answer, result.citations);
      progress.textContent = result.response_type === 'local' || result.response_type === 'metadata'
        ? '바로 답변했습니다.'
        : `답변 완료 · ${(result.elapsed_ms / 1000).toFixed(1)}초 (검색 ${(result.retrieval_ms / 1000).toFixed(1)}초 · AI 작성 ${(result.generation_ms / 1000).toFixed(1)}초)`;
    }
  } catch (error) {
    if (selectedMovieId === movieId && selection === selectionSequence) {
      pendingMessage.remove();
      question.value = prompt;
      addNotice(error.code === 'MODEL_RATE_LIMITED'
        ? 'AI 서비스가 요청을 제한해 답변을 받지 못했습니다. 질문은 입력창에 남겨뒀어요. 잠시 뒤 다시 시도해 주세요.'
        : `${error.message} 질문은 입력창에 남겨뒀어요.`);
      progress.textContent = '';
    }
  }
  finally {
    stopWaiting();
    if (selectedMovieId === movieId && selection === selectionSequence) {
      question.disabled = false; send.disabled = false; collect.disabled = false;
      guide.disabled = false; question.focus();
    }
  }
});

guide.addEventListener('click', async () => {
  if (!selectedMovieId) return;
  const movieId = selectedMovieId;
  const selection = selectionSequence;
  question.disabled = true;
  send.disabled = true;
  collect.disabled = true;
  guide.disabled = true;
  startWaiting('근거를 검색하고 관람 가이드를 작성하는 중');
  try {
    const result = await api('/api/guides', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: movieId }),
    });
    if (selectedMovieId === movieId && selection === selectionSequence) {
      conversationId = result.conversation_id;
      addMessage('user', '관람 가이드 생성');
      addMessage('assistant', result.answer, result.citations);
      progress.textContent = `가이드 완료 · ${(result.elapsed_ms / 1000).toFixed(1)}초 (검색 ${(result.retrieval_ms / 1000).toFixed(1)}초 · AI 작성 ${(result.generation_ms / 1000).toFixed(1)}초)`;
    }
  } catch (error) {
    if (selectedMovieId === movieId && selection === selectionSequence) {
      addNotice(error.code === 'MODEL_RATE_LIMITED'
        ? 'AI 서비스가 요청을 제한해 가이드를 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요.'
        : error.message);
      progress.textContent = '';
    }
  }
  finally {
    stopWaiting();
    if (selectedMovieId === movieId && selection === selectionSequence) {
      question.disabled = false; send.disabled = false; collect.disabled = false;
      guide.disabled = false; question.focus();
    }
  }
});

collect.addEventListener('click', async () => {
  if (!selectedMovieId || !question.value.trim()) {
    progress.textContent = '수집할 문서와 관련된 질문을 입력하세요.';
    question.focus();
    return;
  }
  const movieId = selectedMovieId;
  const selection = selectionSequence;
  question.disabled = true;
  send.disabled = true;
  collect.disabled = true;
  guide.disabled = true;
  progress.textContent = '문서 수집 작업을 시작하는 중…';
  try {
    const job = await api('/api/collection-jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: movieId, question: question.value.trim() }),
    });
    const stages = {
      queued: '문서 수집 대기 중…',
      searching: '검색어를 만들고 문서를 찾는 중…',
      selecting: '질문과 관련된 문서를 고르는 중…',
      fetching: '선택한 문서의 본문을 가져오는 중…',
      loading_cache: '저장된 문서를 불러오는 중…',
      indexing: '문서를 청크로 나누어 색인하는 중…',
    };
    let state = job;
    while (state.status !== 'done' && state.status !== 'failed') {
      if (selectedMovieId === movieId && selection === selectionSequence) {
        progress.textContent = stages[state.stage] || '문서를 수집하는 중…';
      }
      await new Promise(resolve => setTimeout(resolve, 600));
      state = await api(`/api/jobs/${encodeURIComponent(job.job_id)}`);
    }
    if (state.status === 'failed') throw new Error(state.error_message || '문서 수집에 실패했습니다.');
    const result = state.result;
    if (selectedMovieId === movieId && selection === selectionSequence) {
      const names = result.selected.map(item => `${item.title} (${item.source_type})`).join(', ');
      progress.textContent = `수집 완료: ${result.selected.length}문서 · ${result.indexed_chunks}청크 · 캐시 ${result.cache_hits}건${names ? ` (${names})` : ''}${result.warnings?.length ? ` · ${result.warnings.join(' ')}` : ''}`;
    }
  } catch (error) { if (selectedMovieId === movieId && selection === selectionSequence) progress.textContent = error.message; }
  finally {
    if (selectedMovieId === movieId && selection === selectionSequence) {
      question.disabled = false; send.disabled = false; collect.disabled = false;
      guide.disabled = false; question.focus();
    }
  }
});

discoverForm.addEventListener('submit', async event => {
  event.preventDefault();
  const condition = discoverCondition.value.trim();
  if (condition.length < 2) return;
  discoverSubmit.disabled = true;
  discoverStatus.textContent = '조건에 맞는 후보를 검색하고 선별하는 중…';
  discoverResults.replaceChildren();
  try {
    const result = await api('/api/discover', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ condition }),
    });
    for (const movie of result.items) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `${movie.title} (${movie.year || '연도 미상'}) · ${movie.genres.join(', ')}`;
      button.addEventListener('click', () => selectMovie(movie.movie_id));
      item.append(button);
      discoverResults.append(item);
    }
    discoverStatus.textContent = result.items.length
      ? `후보 ${result.candidate_count}편 중 조건에 맞는 ${result.items.length}편을 찾았습니다.`
      : '조건에 맞는 영화를 찾지 못했습니다.';
  } catch (error) { discoverStatus.textContent = error.message; }
  finally { discoverSubmit.disabled = false; }
});

search.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadMovies(true).catch(error => { searchStatus.textContent = error.message; }), 250);
});
searchForm.addEventListener('submit', async event => {
  event.preventDefault();
  clearTimeout(searchTimer);
  try {
    const result = await loadMovies(true);
    if (result?.total === 1) await selectMovie(result.items[0].movie_id);
  } catch (error) { searchStatus.textContent = error.message; }
});
more.addEventListener('click', () => { page += 1; loadMovies().catch(error => { progress.textContent = error.message; }); });
toggleLibrary.addEventListener('click', () => {
  document.body.classList.toggle('library-open');
  if (document.body.classList.contains('library-open')) search.focus();
});
loadMovies(true).catch(error => { progress.textContent = error.message; });
api('/api/index/status').then(status => {
  document.querySelector('#index-status').textContent = status.ready
    ? `색인: 영화 ${status.movie_meta}건 · 문서 청크 ${status.movie_docs}건`
    : '색인이 없습니다. 먼저 색인 스크립트를 실행하세요.';
}).catch(() => { document.querySelector('#index-status').textContent = '색인 상태를 확인하지 못했습니다.'; });
