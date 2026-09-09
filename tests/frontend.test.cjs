// Testes de comportamento sem navegador, rede ou dependências extras.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const calls = [];
  const timers = [];
  const context = vm.createContext({
    URL,
    console: { error() {}, warn() {} },
    document: { getElementById: id => nodes.get(id) || null },
    fetch: async (...args) => { calls.push(args); return { ok: true }; },
    setInterval: fn => { timers.push(fn); return timers.length; },
    clearInterval() {}, clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync('frontend/job-model.js', 'utf8'), context);
  const source = fs.readFileSync('frontend/app.js', 'utf8').split('// Instantiate global app instance')[0];
  vm.runInContext(source + '\nglobalThis.app = Object.create(RadarApp.prototype);', context);
  const app = context.app;
  Object.assign(app, {
    jobs: [{ id: '123', status: 'nova', title: 'Analista', company: 'Empresa' }],
    render() {}, updateStats() {}, showToast(message, trash) { this.lastToast = message; this.trash = trash; },
    closeDetail() {}, loadJobs() { this.loaded = true; },
  });
  return { context, app, nodes, calls, timers };
}

test('desfazer descarte restaura o status também no servidor', async () => {
  const { app, calls } = setup();
  await app.toggleHide('123');
  assert.equal(app.jobs[0].status, 'oculta');
  await app.undo();
  assert.equal(app.jobs[0].status, 'nova');
  assert.equal(calls[1][0], '/api/jobs/123/status');
  assert.deepEqual(JSON.parse(calls[1][1].body), { status: 'nova' });
});

test('erro de status reverte vaga e contadores', async () => {
  const { app, context } = setup();
  let statsUpdates = 0;
  app.updateStats = () => statsUpdates++;
  context.fetch = async () => ({ ok: false });
  await app.updateJobStatus('123', 'enviada');
  assert.equal(app.jobs[0].status, 'nova');
  assert.equal(statsUpdates, 2);
});

test('recarregar página com busca ativa retoma acompanhamento até concluir', async () => {
  const { app, context, timers } = setup();
  context.fetch = async () => ({ ok: true, json: async () => ({ is_running: true }) });
  await app.checkScanStatus();
  await app.checkScanStatus();
  assert.equal(timers.length, 1);
  context.fetch = async () => ({ ok: true, json: async () => ({ is_running: false }) });
  await app.checkScanStatus();
  assert.equal(app.scanInterval, null);
  assert.equal(app.loaded, true);
});

test('salvar consultas não sobrescreve exclusões nem faz GET redundante', async () => {
  const { app, calls } = setup();
  await app.saveQueries();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].method, 'POST');
  assert.equal(Object.hasOwn(JSON.parse(calls[0][1].body), 'excluded_keywords'), false);
});

test('salvar exclusões preserva as consultas no servidor', async () => {
  const { app, calls } = setup();
  await app.saveExclusions();
  assert.equal(calls.length, 1);
  assert.deepEqual(Object.keys(JSON.parse(calls[0][1].body)), ['excluded_keywords']);
});

test('links externos aceitam apenas HTTP e HTTPS', () => {
  const { context } = setup();
  assert.equal(context.JobModel.safeUrl('javascript:alert(1)'), '#');
  assert.equal(context.JobModel.safeUrl('data:text/html,bad'), '#');
  assert.equal(context.JobModel.safeUrl('https://example.com/jobs/123'), 'https://example.com/jobs/123');
});

test('ausência de justificativa não inventa aderência de 75%', () => {
  const { context } = setup();
  const reasons = context.JobModel.parseReasons({ match_score: 0 }).reasons[0];
  assert.match(reasons, /0%/);
  assert.doesNotMatch(reasons, /75%/);
});

test('dados ausentes permanecem explicitamente não informados', () => {
  const { context } = setup();
  const job = context.JobModel.toViewJob({ id: '1', status: 'nova' });
  assert.equal(job.place, 'Local não informado');
  assert.equal(job.salary, 'Salário não informado');
  assert.equal(job.level, 'Não informado');
  assert.equal(job.posted, 'data não informada');
  assert.equal(job.workplace, 'nao_informado');
  assert.equal(job.pillar, 'outros');
});

test('status legados aparecem em uma das três colunas atuais', () => {
  const { context } = setup();
  assert.equal(context.JobModel.normalizeStatus('analisada'), 'nova');
  assert.equal(context.JobModel.normalizeStatus('selecionada'), 'nova');
  assert.equal(context.JobModel.normalizeStatus('entrevista'), 'enviada');
});

test('botão de limpeza possui ação associada', () => {
  const { app, context, nodes } = setup();
  const events = [];
  nodes.set('btn-purge-db', { addEventListener: (...args) => events.push(args) });
  context.document.querySelectorAll = () => [];
  context.window = { addEventListener() {} };
  let triggered = false;
  app.purgeDatabase = () => { triggered = true; };
  app.bindEvents();
  events[0][1]();
  assert.equal(triggered, true);
});

test('desfazer descarte após atualização restaura a vaga carregada', async () => {
  const { app, calls } = setup();
  await app.toggleHide('123');
  app.jobs = [{ id: '123', status: 'oculta', company: 'Empresa' }];
  await app.undo();
  assert.equal(app.jobs.length, 1);
  assert.equal(app.jobs[0].status, 'nova');
  assert.equal(calls[1][1].method, 'POST');
});

test('kanban exibe somente as três colunas principais', () => {
  const html = fs.readFileSync('frontend/index.html', 'utf8');
  const columns = [...html.matchAll(/class="kanban-col" data-col="([^"]+)"/g)]
    .map(match => match[1]);
  assert.deepEqual(columns, ['nova', 'enviada', 'oculta']);
});
