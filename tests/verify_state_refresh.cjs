const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function extract(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert(from >= 0 && to > from, `Could not find ${start}`);
  return source.slice(from, to);
}
// Execute the real polling functions, with requests held until the test releases them.
const functions = extract('async function refresh(', '\nfunction drawNav(')
  + extract('async function syncVisibleState(', '\nsetInterval(syncVisibleState,');
function fixture() {
  const requests = [], counts = {draw: 0, nav: 0, editor: 0, flow: 0};
  const elements = {'#service-state': {innerHTML: ''}, '#modal': {open: false}};
  const context = {
    refreshSequence: 0, stateEpoch: 0, state: null, deployment: 'local',
    view: 'guide', location: {hash: '#guide'}, pollBusy: false, modalBusy: false,
    document: {hidden: false, activeElement: {matches: () => false}},
    $: selector => selector === 'dialog[open]' ? (elements['#modal'].open ? elements['#modal'] : null) : elements[selector],
    api: endpoint => {
      assert.equal(endpoint, '/state');
      return new Promise(resolve => requests.push(resolve));
    },
    draw: () => {counts.draw++;}, drawNav: () => {counts.nav++;},
    syncEditorState: () => {counts.editor++;},
    refreshArticleFlow: async () => {counts.flow++;},
  };
  vm.createContext(context);
  vm.runInContext(functions, context);
  return {context, counts, elements, requests,
    reply: (index = 0) => requests[index]({worker: {alive: true, error: ''}, posts: [], revision: index + 1})};
}

test('a pending poll cannot redraw the destination settings page and discard its focus', async () => {
  const f = fixture(), pending = f.context.syncVisibleState();
  const input = {value: 'unsaved fixture', matches: () => true};
  f.context.view = 'settings'; f.context.location.hash = '#settings';
  f.context.document.activeElement = input;
  f.reply(); await pending;
  assert.equal(f.counts.draw, 0);
  assert.equal(f.context.document.activeElement, input);
  assert.equal(input.value, 'unsaved fixture');
  assert.equal(f.context.state.revision, 1, 'still receive current state without replacing the DOM');
  assert.equal(f.counts.nav, 1);
  assert.equal(f.context.pollBusy, false);
});

test('a route change suppresses an old redraw even when both pages normally allow polling', async () => {
  const f = fixture(), pending = f.context.syncVisibleState();
  f.context.view = 'home'; f.context.location.hash = '#home';
  f.reply(); await pending;
  assert.equal(f.counts.draw, 0);
});

test('hash changes are checked before the route renderer has updated view', async () => {
  const f = fixture(), pending = f.context.syncVisibleState();
  f.context.location.hash = '#settings';
  f.reply(); await pending;
  assert.equal(f.counts.draw, 0);
});

test('new input focus, editable content, dialogs, and hidden or busy pages prevent delayed redraw', async () => {
  const cases = [
    f => {f.context.document.activeElement = {matches: () => true};},
    f => {f.context.document.activeElement = {matches: () => false, isContentEditable: true};},
    f => {f.elements['#modal'].open = true;},
    f => {f.context.document.hidden = true;},
    f => {f.context.modalBusy = true;},
  ];
  for (const change of cases) {
    const f = fixture(), pending = f.context.syncVisibleState();
    change(f); f.reply(); await pending;
    assert.equal(f.counts.draw, 0);
    assert.equal(f.context.state.revision, 1);
  }
});

test('a protected page does not redraw even without an input focused', async () => {
  const f = fixture(); f.context.view = 'settings'; f.context.location.hash = '#settings';
  const pending = f.context.syncVisibleState(); f.reply(); await pending;
  assert.equal(f.counts.draw, 0);
});

test('an unchanged safe page still redraws and subsequent polls can proceed', async () => {
  const f = fixture();
  let pending = f.context.syncVisibleState(); f.reply(); await pending;
  assert.equal(f.counts.draw, 1);
  assert.equal(f.counts.editor, 1);
  assert.equal(f.counts.flow, 1);
  pending = f.context.syncVisibleState(); f.reply(1); await pending;
  assert.equal(f.counts.draw, 2);
});

test('existing explicit refresh(true/false) behavior is retained', async () => {
  const f = fixture();
  let pending = f.context.refresh(false); f.reply(); await pending;
  assert.equal(f.counts.draw, 0); assert.equal(f.counts.nav, 1);
  pending = f.context.refresh(true); f.reply(1); await pending;
  assert.equal(f.counts.draw, 1);
});

test('superseded responses cannot invoke the render predicate or replace newer state', async () => {
  const f = fixture(); let invoked = 0;
  const first = f.context.refresh(() => {invoked++; return true;});
  const second = f.context.refresh(false);
  f.reply(1); await second; f.reply(0); await first;
  assert.equal(invoked, 0); assert.equal(f.context.state.revision, 2);
  const third = f.context.refresh(() => {invoked++; return true;});
  f.context.stateEpoch++; f.reply(2); await third;
  assert.equal(invoked, 0); assert.equal(f.context.state.revision, 2);
});
