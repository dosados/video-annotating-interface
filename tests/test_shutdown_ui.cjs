const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const script = fs.readFileSync('src/video_annotating_interface/static/shutdown.js', 'utf8');
async function check({save = true, confirmed = true, fail = false} = {}) {
  const calls = [], button = {disabled: false}, control = {disabled: true};
  let stopped = false;
  const context = vm.createContext({
    document: {
      getElementById: () => button, querySelectorAll: () => [button, control],
      addEventListener() {}, removeEventListener() {},
      createElement: () => ({setAttribute() {}}),
      body: {replaceChildren() {stopped = true;}}
    },
    window: {prepareShutdown: async () => {calls.push('save'); return save;}},
    confirm: () => confirmed, alert: () => calls.push('error'),
    fetch: async () => {calls.push('shutdown'); return {ok: !fail, json: async () => ({})};}
  });
  vm.runInContext(script, context);
  await button.onclick();
  assert.equal(button.disabled, false);
  assert.equal(control.disabled, true);
  return {calls, stopped};
}
(async () => {
  assert.deepEqual(await check(), {calls: ['save', 'shutdown'], stopped: true});
  assert.deepEqual(await check({save: false}), {calls: ['save'], stopped: false});
  assert.deepEqual(await check({confirmed: false}), {calls: [], stopped: false});
  assert.deepEqual(await check({fail: true}), {calls: ['save', 'shutdown', 'error'], stopped: false});
  console.log('Shutdown UI checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
