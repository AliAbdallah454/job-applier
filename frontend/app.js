const $ = (id) => document.getElementById(id);
const form = $('filters');
let jobs = [];
let cursor = null;
let selectedId = null;
let requestNumber = 0;
let controller = null;
let currentFilters = new URLSearchParams();

function label(value) {
  if (!value) return 'Unspecified';
  const names = { ai_engineer: 'AI engineer', ml_engineer: 'ML engineer', devops_engineer: 'DevOps engineer', qa_engineer: 'QA engineer', fixed_term: 'Fixed-term', non_technical: 'Non-technical' };
  return names[value] || value.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function tags(job) {
  const node = element('div', 'tags');
  node.append(element('span', 'tag', label(job.seniority)), element('span', 'tag', label(job.category)), element('span', 'tag contract', label(job.contract_type)));
  return node;
}

function companyHeader(job) {
  const node = element('div', 'card-top');
  const initials = (job.company || '?').split(/\s+/).slice(0, 2).map((word) => word[0]).join('').toUpperCase();
  node.append(element('span', 'company-avatar', initials), element('span', 'company-name', job.company || 'Company unspecified'), element('span', 'job-source', job.source || ''));
  return node;
}

function showDetails(job) {
  selectedId = job.job_id;
  for (const card of $('jobs').children) {
    const selected = card.dataset.id === selectedId;
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-pressed', String(selected));
  }
  const detail = $('detail');
  detail.replaceChildren(companyHeader(job), element('h2', '', job.title), element('p', 'location', job.location || 'Location unspecified'), tags(job));
  const url = safeUrl(job.application_url) || safeUrl(job.posting_url);
  if (url) {
    const link = element('a', 'primary', 'View original posting ↗');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    detail.append(link);
  }
  detail.append(element('h3', 'description-heading', 'About the role'), element('div', 'description', job.description || 'No description provided. Open the original posting for details.'));
}

function render() {
  $('count').textContent = `${jobs.length} loaded`;
  $('jobs').replaceChildren();
  for (const job of jobs) {
    const card = element('button', 'job-card');
    card.type = 'button';
    card.dataset.id = job.job_id;
    card.setAttribute('aria-pressed', String(job.job_id === selectedId));
    card.classList.toggle('selected', job.job_id === selectedId);
    card.append(companyHeader(job), element('h3', '', job.title), element('p', 'location', job.location || 'Location unspecified'), tags(job));
    const bottom = element('div', 'card-bottom');
    let published = job.published_text || '';
    if (!published && job.published_at) {
      const date = new Date(job.published_at);
      if (!Number.isNaN(date.getTime())) published = `Posted ${date.toLocaleDateString()}`;
    }
    bottom.append(element('span', '', published || 'Publication date unavailable'), element('span', 'card-arrow', '↗'));
    card.append(bottom);
    card.addEventListener('click', () => { showDetails(job); });
    $('jobs').append(card);
  }
  $('empty').hidden = jobs.length !== 0;
  $('empty-message').textContent = cursor ? 'More records are available. Load more to keep looking with these filters.' : 'Try another category or broaden your experience levels.';
  $('load-more').hidden = !cursor;
  if (!jobs.length) {
    $('detail').replaceChildren(element('div', 'detail-placeholder', 'Select an opportunity to see its details.'));
  } else {
    showDetails(jobs.find((job) => job.job_id === selectedId) || jobs[0]);
  }
}

function readFilters() {
  return new URLSearchParams({
    q: $('search').value.trim(), tech: $('tech').value, category: $('category').value,
    contract: $('contract').value,
    seniority: [...form.querySelectorAll('[name=seniority]:checked')].map((input) => input.value).join(','),
  });
}

async function load(reset = false) {
  if (controller) controller.abort();
  controller = new AbortController();
  const number = ++requestNumber;
  if (reset) {
    currentFilters = readFilters(); jobs = []; cursor = null; selectedId = null;
    render(); $('empty').hidden = true;
  }
  const params = new URLSearchParams(currentFilters);
  if (cursor) params.set('cursor', cursor);
  $('status').className = '';
  $('status').textContent = 'Finding opportunities…';
  $('apply').disabled = true;
  $('load-more').disabled = true;
  try {
    const response = await fetch(`/api/jobs?${params}`, { signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load jobs. Please try again.');
    if (number !== requestNumber) return;
    const seen = new Set(jobs.map((job) => job.job_id));
    jobs.push(...data.jobs.filter((job) => !seen.has(job.job_id)));
    cursor = data.next_cursor;
    render();
    $('status').textContent = cursor ? 'Showing loaded matches. Load more to search the next records.' : (jobs.length ? 'All matching opportunities in this search have been loaded.' : 'No matching opportunities found.');
  } catch (error) {
    if (error.name === 'AbortError' || number !== requestNumber) return;
    $('status').className = 'error';
    $('status').textContent = error.message;
    if (reset) $('empty').hidden = true;
  } finally {
    if (number === requestNumber) { $('apply').disabled = false; $('load-more').disabled = false; }
  }
}

form.addEventListener('submit', (event) => { event.preventDefault(); load(true); });
$('reset').addEventListener('click', () => { form.reset(); load(true); });
$('load-more').addEventListener('click', () => load());

async function start() {
  try {
    const response = await fetch('/api/options');
    if (!response.ok) throw new Error('Could not initialize filters. Refresh to try again.');
    const options = await response.json();
    for (const [id, values] of [['category', options.categories], ['contract', options.contract_types]]) {
      for (const value of values) $(id).add(new Option(label(value), value));
    }
    $('contract').add(new Option('Unspecified', 'unknown'));
    $('demo-badge').hidden = !options.demo;
    await load(true);
  } catch (error) {
    $('status').className = 'error'; $('status').textContent = error.message;
  }
}
start();
