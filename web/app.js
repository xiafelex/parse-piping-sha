const state = { project: null, activePage: null };
const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function fileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function artifactUrl(path) {
  return `/projects/${state.project.id}/${path}`;
}

async function loadProjects(preferredId) {
  const { projects } = await request('/api/projects');
  const select = $('#project-select');
  select.innerHTML = projects.length
    ? projects.map((project) => `<option value="${project.id}">${escapeHtml(project.name)}</option>`).join('')
    : '<option value="">尚未创建项目</option>';
  if (!projects.length) {
    state.project = null;
    renderProject();
    return;
  }
  const id = preferredId || state.project?.id || projects[0].id;
  select.value = id;
  state.project = await request(`/api/projects/${id}`);
  renderProject();
}

function renderProject() {
  const project = state.project;
  $('#welcome').hidden = Boolean(project);
  $('#workspace').hidden = !project;
  $('#project-state').textContent = project ? `本地项目：${project.id}` : '';
  if (!project) return;
  const sources = project.sources || [];
  $('#analyze').disabled = !sources.some((source) => source.kind === 'SHA');
  $('#sources').innerHTML = sources.length ? sources.map((source) => `
    <article class="source-card">
      <div><span class="kind ${source.kind.toLowerCase()}">${source.kind}</span><strong>${escapeHtml(source.display_name)}</strong></div>
      <p>${fileSize(source.bytes)} · SHA-256 <code>${source.sha256.slice(0, 16)}…</code></p>
    </article>`).join('') : '<p class="empty">还没有导入文件。</p>';
  renderAnalysis(project.analysis);
}

function renderMetrics(items) {
  const target = $('#metrics');
  target.innerHTML = '';
  for (const [label, value] of items) {
    const fragment = $('#metric-template').content.cloneNode(true);
    fragment.querySelector('p').textContent = label;
    fragment.querySelector('strong').textContent = value;
    target.append(fragment);
  }
}

function renderAnalysis(analysis) {
  $('#analysis').hidden = !analysis;
  $('#viewer').hidden = !analysis;
  if (!analysis) return;
  const pcf = analysis.pcf || [];
  const sha = analysis.sha || [];
  const pairs = analysis.pairs || [];
  renderMetrics([
    ['PCF 文件', pcf.length], ['SHA 文件', sha.length],
    ['PCF 构件', pcf.reduce((total, item) => total + item.component_count, 0)],
    ['ISO 页面', sha.reduce((total, item) => total + item.logical_pages, 0)],
    ['SHA UCI', sha.reduce((total, item) => total + item.uci_count, 0)],
    ['直接 UCI 关联', pairs.reduce((total, item) => total + item.link_coverage.direct_uci_links, 0)]
  ]);
  $('#analysis-time').textContent = `分析完成：${new Date(analysis.generated_at).toLocaleString()}`;
  $('#confidence-notice').textContent = analysis.confidence_notice;
  $('#pcf-summary').innerHTML = pcf.length ? pcf.map((item) => `<p><strong>${escapeHtml(item.display_name)}</strong><br>${escapeHtml(item.pipeline || '未识别管线')} · ${item.component_count} 个构件 · ${item.uci_count} 个 UCI</p>`).join('') : '<p>未导入 PCF，因此只有 SHA 出图模型。</p>';
  $('#sha-summary').innerHTML = sha.map((item) => `<p><strong>${escapeHtml(item.display_name)}</strong><br>${item.logical_pages} 页 · ${item.uci_count} 个 UCI · ${item.dynamic_graphic_count} 个动态图元</p>`).join('');
  $('#split-summary').innerHTML = pairs.length ? pairs.map((item) => {
    const candidates = item.same_line_interfaces.length ? item.same_line_interfaces.map(escapeHtml).join('<br>') : '未发现跨页候选';
    const pairing = item.pairing_confidence === 'recommended' ? '推荐配对' : item.pairing_confidence === 'unresolved' ? '未确认配对' : '候选配对';
    return `<p><strong>${escapeHtml(item.pipeline || 'PCF/SHA 配对')}</strong> · ${pairing}<br>${candidates}<br><a target="_blank" href="${artifactUrl(item.split_report)}">打开完整分图报告</a></p>`;
  }).join('') : '<p>导入 PCF 后可计算工程坐标关联。</p>';
  $('#unresolved').innerHTML = `<ul>${analysis.unresolved.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
  renderPages(sha.flatMap((item) => item.pages.map((page) => ({ ...page, source: item.display_name }))));
}

function renderPages(pages) {
  const tabs = $('#page-tabs');
  tabs.innerHTML = pages.map((page, index) => `<button class="page-tab ${index === 0 ? 'active' : ''}" data-page="${index}">${escapeHtml(page.source)} · 第 ${page.page} 页</button>`).join('');
  tabs.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => showPage(pages[Number(button.dataset.page)], button)));
  if (pages.length) showPage(pages[0], tabs.querySelector('button'));
}

async function showPage(page, button) {
  state.activePage = page;
  $('#page-tabs').querySelectorAll('button').forEach((tab) => tab.classList.toggle('active', tab === button));
  $('#svg-canvas').innerHTML = '<p class="loading">正在载入 SHA 派生 SVG…</p>';
  const source = await fetch(artifactUrl(page.svg)).then((response) => response.text());
  $('#svg-canvas').innerHTML = source;
  $('#trace-link').href = artifactUrl(page.trace);
  $('#trace-link').hidden = false;
  $('#svg-canvas svg').addEventListener('click', inspectSvgElement);
}

function inspectSvgElement(event) {
  const element = event.target;
  const attributes = Object.fromEntries([...element.attributes].map((attribute) => [attribute.name, attribute.value]));
  $('#inspect-title').textContent = element.tagName.toLowerCase();
  $('#inspect-description').textContent = attributes['data-uci']
    ? '此图元保留了 SHA 动态属性链中的 UCI。请结合 Trace JSON 判断它是直接关联还是空间候选。'
    : '这是 SHA 解析出的图纸图元；它未必对应一个 PCF 构件。';
  $('#inspect-data').textContent = JSON.stringify(attributes, null, 2);
}

async function createProject() {
  const name = window.prompt('项目名称', `ISO 项目 ${new Date().toLocaleDateString()}`);
  if (name === null) return;
  const project = await request('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
  await loadProjects(project.id);
}

async function uploadFiles(files) {
  if (!state.project || !files.length) return;
  $('#import-status').textContent = `正在导入 ${files.length} 个文件…`;
  try {
    for (const file of files) {
      await request(`/api/projects/${state.project.id}/imports`, { method: 'POST', headers: { 'X-Filename': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' }, body: file });
    }
    $('#import-status').textContent = '导入完成，已记录源文件哈希。';
    await loadProjects(state.project.id);
  } catch (error) {
    $('#import-status').textContent = error.message;
  }
}

async function analyze() {
  if (!state.project) return;
  $('#analyze').disabled = true;
  $('#import-status').textContent = '正在解析 SHA、生成 SVG 和 Trace…';
  try {
    state.project = await request(`/api/projects/${state.project.id}/analyze`, { method: 'POST' });
    $('#import-status').textContent = '分析完成。';
    renderProject();
  } catch (error) {
    $('#import-status').textContent = error.message;
  } finally {
    $('#analyze').disabled = !(state.project?.sources || []).some((source) => source.kind === 'SHA');
  }
}

$('#new-project').addEventListener('click', createProject);
$('#welcome-create').addEventListener('click', createProject);
$('#project-select').addEventListener('change', (event) => loadProjects(event.target.value));
$('#file-input').addEventListener('change', (event) => uploadFiles([...event.target.files]));
$('#analyze').addEventListener('click', analyze);
document.querySelector('.drop-zone').addEventListener('dragover', (event) => event.preventDefault());
document.querySelector('.drop-zone').addEventListener('drop', (event) => { event.preventDefault(); uploadFiles([...event.dataTransfer.files]); });
loadProjects().catch((error) => { $('#project-state').textContent = error.message; });
