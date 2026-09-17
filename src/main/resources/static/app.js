/* ================= 白山面试辅助 Agent 前端逻辑 ================= */
'use strict';

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

let selectedResumeId = null;
let currentSessionId = null;
let interviewActive = false;
const resumesById = {};   // id -> fileName，用于显示当前面试所用简历

/* ---------- 当前选中简历指示 ---------- */
function updateSelectedResume() {
  const label = $('#iv-current-resume');
  const btn = $('#btn-create-session');
  if (selectedResumeId && resumesById[selectedResumeId]) {
    label.textContent = resumesById[selectedResumeId] + '（' + selectedResumeId.slice(0, 8) + '…）';
    label.style.color = 'var(--ok)';
    btn.disabled = false;
  } else {
    label.textContent = '未选择（请先在「简历分析」上传并分析）';
    label.style.color = '';
    btn.disabled = true;
  }
}

/* ---------- 工具：提示 ---------- */
let toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), ms);
}

/* ---------- 工具：流式 SSE（fetch + 逐段解析 data: 行） ---------- */
async function streamPost(url, body, onData, onDone, onError) {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try { msg = (await resp.json()).message || msg; } catch (e) { /* ignore */ }
      onError(new Error(msg));
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const event = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of event.split('\n')) {
          if (line.startsWith('data:')) onData(line.slice(5).trim());
        }
      }
    }
    onDone();
  } catch (e) {
    onError(e);
  }
}

async function api(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    let msg = 'HTTP ' + resp.status;
    try { msg = (await resp.json()).message || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return resp.json();
}

/* ================= Tab 切换 ================= */
document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
    $('#tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'knowledge') refreshKbList();
    if (btn.dataset.tab === 'resume') refreshResumeList();
  });
});

/* ================= 简历 ================= */
async function refreshResumeList() {
  const box = $('#resume-list');
  let resumes;
  try { resumes = await api('/api/resumes'); } catch (e) { toast('加载简历失败: ' + e.message); return; }
  box.innerHTML = '';
  for (const r of resumes) { resumesById[r.id] = r.fileName; }
  if (resumes.length === 0) { box.appendChild(el('div', 'muted', '暂无简历，请先上传。')); return; }
  for (const r of resumes) {
    const item = el('div', 'item');
    const left = el('div');
    const name = el('div', '', r.fileName);
    const meta = el('div', 'meta', '上传于 ' + (r.createdAt || '').slice(0, 16).replace('T', ' '));
    if (r.analyzed) meta.appendChild(el('span', 'tag done', '已分析 ' + (r.overallScore ?? '') + ' 分'));
    else meta.appendChild(el('span', 'tag', '未分析'));
    left.append(name, meta);
    const ops = el('div', 'ops');
    if (!r.analyzed) {
      const analyzeBtn = el('button', 'primary', 'AI 分析');
      analyzeBtn.addEventListener('click', () => analyzeResume(r.id, analyzeBtn));
      ops.appendChild(analyzeBtn);
    } else {
      const viewBtn = el('button', '', '查看分析');
      viewBtn.addEventListener('click', () => showAnalysis(r.id));
      ops.appendChild(viewBtn);
      const useBtn = el('button', 'primary', '用它面试');
      useBtn.addEventListener('click', () => {
        selectedResumeId = r.id;
        updateSelectedResume();
        toast('已选择简历：' + r.fileName + '，去「模拟面试」开始吧');
        document.querySelector('.tab[data-tab="interview"]').click();
      });
      ops.appendChild(useBtn);
    }
    const delBtn = el('button', 'ghost', '删除');
    delBtn.addEventListener('click', async () => {
      if (!confirm('确定删除该简历？')) return;
      await api('/api/resumes/' + r.id, { method: 'DELETE' });
      refreshResumeList();
    });
    ops.appendChild(delBtn);
    item.append(left, ops);
    box.appendChild(item);
  }
}

async function analyzeResume(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '分析中（约 10-30 秒）…'; }
  try {
    await api('/api/resumes/' + id + '/analyze', { method: 'POST' });
    showAnalysis(id);
    refreshResumeList();
    toast('简历分析完成，已自动选中用于面试');
  } catch (e) {
    toast('分析失败: ' + e.message, 5000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'AI 分析'; }
  }
}

function showAnalysis(id) {
  api('/api/resumes/' + id + '/analysis').then((a) => {
    $('#an-name').textContent = a.candidateName || '候选人';
    $('#an-position').textContent = '求职目标：' + (a.targetPosition || '未识别');
    $('#an-score').textContent = a.overallScore;
    $('#an-summary').textContent = a.summary || '';
    renderChips('#an-skills', a.skills);
    renderList('#an-strengths', a.strengths);
    renderList('#an-weaknesses', a.weaknesses);
    renderList('#an-suggestions', a.suggestions);
    const dims = $('#an-dims');
    dims.innerHTML = '';
    for (const d of (a.dimensions || [])) {
      const row = el('div', 'dim-row');
      row.append(el('span', 'name', d.name));
      const bar = el('div', 'dim-bar');
      const fill = el('div', 'dim-fill');
      fill.style.width = Math.max(0, Math.min(100, d.score)) + '%';
      bar.appendChild(fill);
      row.append(bar);
      row.append(el('span', 'val', d.score + ''));
      dims.appendChild(row);
    }
    $('#analysis-card').classList.remove('hidden');
    selectedResumeId = id;
    updateSelectedResume();
    $('#btn-start-interview').onclick = () => {
      updateSelectedResume();
      document.querySelector('.tab[data-tab="interview"]').click();
    };
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }).catch((e) => toast('加载分析失败: ' + e.message));
}

function renderChips(sel, items) {
  const box = $(sel);
  box.innerHTML = '';
  for (const s of (items || [])) box.appendChild(el('span', '', s));
}
function renderList(sel, items) {
  const box = $(sel);
  box.innerHTML = '';
  for (const s of (items || [])) box.appendChild(el('li', '', s));
}

$('#btn-upload-resume').addEventListener('click', async () => {
  const file = $('#resume-file').files[0];
  if (!file) { toast('请先选择文件'); return; }
  const fd = new FormData();
  fd.append('file', file);
  const btn = $('#btn-upload-resume');
  btn.disabled = true; btn.textContent = '上传解析中…';
  try {
    const r = await api('/api/resumes/upload', { method: 'POST', body: fd });
    toast('上传成功：' + r.fileName + '，开始 AI 分析…');
    $('#resume-file').value = '';
    refreshResumeList();
    if (!r.analyzed) analyzeResume(r.id, null);
  } catch (e) {
    toast('上传失败: ' + e.message, 5000);
  } finally {
    btn.disabled = false; btn.textContent = '上传';
  }
});

/* ================= 模拟面试 ================= */
function appendChat(cls, who, text) {
  const box = $('#chat-box');
  const m = el('div', 'msg ' + cls);
  if (who) m.appendChild(el('div', 'who', who));
  const body = el('div');
  body.textContent = text;
  m.appendChild(body);
  box.appendChild(m);
  box.scrollTop = box.scrollHeight;
  return m;
}

async function askQuestion() {
  if (!currentSessionId) return;
  const msg = appendChat('interviewer', '面试官', '');
  const body = msg.querySelector('div:last-child');
  msg.classList.add('cursor');
  try {
    await streamPost('/api/interviews/' + currentSessionId + '/ask', null,
      (chunk) => { body.textContent += chunk; $('#chat-box').scrollTop = $('#chat-box').scrollHeight; },
      () => {
        msg.classList.remove('cursor');
        $('#answer-area').classList.remove('hidden');
        $('#btn-create-session').disabled = true;
      },
      (err) => {
        msg.classList.remove('cursor');
        if (err.message.includes('全部提出') || err.message.includes('已结束')) {
          finishInterview();
        } else {
          toast('出题失败: ' + err.message, 5000);
        }
      });
  } catch (e) { /* streamPost 已处理 */ }
}

async function submitAnswer() {
  const content = $('#iv-answer').value.trim();
  if (!content || !currentSessionId) return;
  const btn = $('#btn-submit-answer');
  btn.disabled = true; btn.textContent = '评估中…';
  appendChat('user', '我', content);
  $('#iv-answer').value = '';
  try {
    const ev = await api('/api/interviews/' + currentSessionId + '/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const box = $('#eval-box');
    box.classList.remove('hidden');
    $('#eval-body').innerHTML = '';
    $('#eval-body').appendChild(el('div', 'score-line',
      '得分 ' + ev.score + ' / 10（' + (ev.level || '') + '）'));
    $('#eval-body').appendChild(el('div', '', ev.feedback || ''));
    if (ev.strengths && ev.strengths.length) {
      $('#eval-body').appendChild(el('div', 'muted', '亮点：' + ev.strengths.join('；')));
    }
    if (ev.improvements && ev.improvements.length) {
      $('#eval-body').appendChild(el('div', 'muted', '改进：' + ev.improvements.join('；')));
    }
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    askQuestion();
  } catch (e) {
    toast('评估失败: ' + e.message, 5000);
  } finally {
    btn.disabled = false; btn.textContent = '提交回答';
  }
}

async function finishInterview() {
  if (!currentSessionId) return;
  $('#answer-area').classList.add('hidden');
  $('#btn-finish').disabled = true;
  try {
    const s = await api('/api/interviews/' + currentSessionId + '/finish', { method: 'POST' });
    $('#summary-box').classList.remove('hidden');
    const body = $('#summary-body');
    body.innerHTML = '';
    body.appendChild(el('div', 'big', '综合得分：' + s.overallScore + ' / 100 · ' + (s.level || '')));
    body.appendChild(el('div', '', s.overallEvaluation || ''));
    if (s.strengths && s.strengths.length) body.appendChild(el('div', 'muted', '优势：' + s.strengths.join('；')));
    if (s.weaknesses && s.weaknesses.length) body.appendChild(el('div', 'muted', '待提升：' + s.weaknesses.join('；')));
    if (s.developmentSuggestions && s.developmentSuggestions.length) {
      body.appendChild(el('div', 'muted', '建议：' + s.developmentSuggestions.join('；')));
    }
    if (s.hiringAdvice) body.appendChild(el('div', '', s.hiringAdvice));
    $('#btn-download-pdf').href = '/api/reports/interview/' + currentSessionId + '/pdf';
    appendChat('system', '', '🏁 面试结束，已生成总结。可下载 PDF 报告。');
    interviewActive = false;
  } catch (e) {
    toast('总结失败: ' + e.message, 5000);
    $('#btn-finish').disabled = false;
  }
}

$('#btn-create-session').addEventListener('click', async () => {
  if (!selectedResumeId) { toast('请先选择已分析的简历'); return; }
  const btn = $('#btn-create-session');
  btn.disabled = true; btn.textContent = '创建中…';
  try {
    const s = await api('/api/interviews', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resumeId: selectedResumeId, mode: $('#iv-mode').value, jdText: $('#iv-jd').value.trim() || null }),
    });
    currentSessionId = s.id;
    appendChat('system', '', '面试基于简历：' + (resumesById[selectedResumeId] || selectedResumeId));
    interviewActive = true;
    $('#chat-box').innerHTML = '';
    $('#eval-box').classList.add('hidden');
    $('#summary-box').classList.add('hidden');
    $('#btn-finish').classList.remove('hidden');
    for (const m of s.messages || []) {
      if (m.kind === 'SYSTEM') appendChat('system', '', m.content);
    }
    $('#iv-progress').textContent = '（共 5 题）';
    askQuestion();
    toast('面试开始，第一题生成中…');
  } catch (e) {
    toast('创建面试失败: ' + e.message, 5000);
  } finally {
    btn.disabled = false; btn.textContent = '开始面试';
  }
});

$('#btn-submit-answer').addEventListener('click', submitAnswer);
$('#iv-answer').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitAnswer(); }
});
$('#btn-finish').addEventListener('click', () => {
  if (confirm('确定结束面试并生成总结吗？')) finishInterview();
});

/* ================= 知识库 ================= */
async function refreshKbList() {
  const box = $('#kb-list');
  let docs;
  try { docs = await api('/api/knowledge/documents'); } catch (e) { toast('加载知识库失败: ' + e.message); return; }
  box.innerHTML = '';
  if (docs.length === 0) { box.appendChild(el('div', 'muted', '知识库为空，上传企业介绍 / 岗位说明等资料。')); return; }
  for (const d of docs) {
    const item = el('div', 'item');
    const left = el('div');
    left.appendChild(el('div', '', d.fileName));
    left.appendChild(el('div', 'meta', d.chunkCount + ' 分块 · ' + (d.createdAt || '').slice(0, 16).replace('T', ' ')));
    const del = el('button', 'ghost', '删除');
    del.addEventListener('click', async () => {
      if (!confirm('删除该文档及其分块？')) return;
      await api('/api/knowledge/documents/' + d.id, { method: 'DELETE' });
      refreshKbList();
    });
    item.append(left, del);
    box.appendChild(item);
  }
}

$('#btn-upload-kb').addEventListener('click', async () => {
  const file = $('#kb-file').files[0];
  if (!file) { toast('请先选择文件'); return; }
  const fd = new FormData();
  fd.append('file', file);
  fd.append('docType', 'KNOWLEDGE');
  const btn = $('#btn-upload-kb');
  btn.disabled = true; btn.textContent = '解析 + 向量化中…';
  try {
    const d = await api('/api/knowledge/documents', { method: 'POST', body: fd });
    toast('入库成功：' + d.fileName + '（' + d.chunkCount + ' 分块）');
    $('#kb-file').value = '';
    refreshKbList();
  } catch (e) {
    toast('入库失败: ' + e.message, 5000);
  } finally {
    btn.disabled = false; btn.textContent = '上传入库';
  }
});

$('#btn-kb-search').addEventListener('click', async () => {
  const q = $('#kb-query').value.trim();
  if (!q) return;
  const box = $('#kb-results');
  box.innerHTML = '';
  box.appendChild(el('div', 'muted', '检索中…'));
  try {
    const hits = await api('/api/knowledge/search?query=' + encodeURIComponent(q) + '&topK=4');
    box.innerHTML = '';
    if (hits.length === 0) { box.appendChild(el('div', 'muted', '未检索到相关内容')); return; }
    for (const h of hits) {
      const item = el('div', 'item');
      const left = el('div');
      left.appendChild(el('div', '', '【' + h.fileName + '】相似度 ' + (h.score * 100).toFixed(1) + '%'));
      left.appendChild(el('div', 'muted', (h.content || '').slice(0, 220)));
      item.appendChild(left);
      box.appendChild(item);
    }
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(el('div', 'muted', '检索失败: ' + e.message));
  }
});
$('#kb-query').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#btn-kb-search').click(); });

/* ================= 智能问答 ================= */
function appendQa(cls, text, streaming) {
  const box = $('#qa-box');
  const m = el('div', 'msg ' + cls);
  m.appendChild(el('div', 'who', cls === 'user' ? '我' : '白山求职助手'));
  const body = el('div');
  body.textContent = text || '';
  m.appendChild(body);
  box.appendChild(m);
  box.scrollTop = box.scrollHeight;
  if (streaming) m.classList.add('cursor');
  return { m, body };
}

$('#btn-qa-send').addEventListener('click', async () => {
  const q = $('#qa-input').value.trim();
  if (!q) return;
  const btn = $('#btn-qa-send');
  btn.disabled = true;
  appendQa('user', q);
  $('#qa-input').value = '';
  const { m, body } = appendQa('interviewer', '', true);
  let done = false;
  try {
    await streamPost('/api/qa/stream', { message: q, resumeId: $('#qa-resume').value || null },
      (chunk) => { body.textContent += chunk; $('#qa-box').scrollTop = $('#qa-box').scrollHeight; },
      () => { done = true; m.classList.remove('cursor'); },
      (err) => { m.classList.remove('cursor'); body.textContent += '\n\n[错误] ' + err.message; });
  } finally {
    btn.disabled = false;
  }
});
$('#qa-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#btn-qa-send').click(); });

/* ================= 初始化 ================= */
async function init() {
  refreshResumeList();
  refreshKbList();
  updateSelectedResume();
  try {
    const resumes = await api('/api/resumes');
    const sel = $('#qa-resume');
    for (const r of resumes) {
      if (!r.analyzed) continue;
      const opt = el('option', '', r.fileName);
      opt.value = r.id;
      sel.appendChild(opt);
    }
  } catch (e) { /* ignore */ }
}
init();
