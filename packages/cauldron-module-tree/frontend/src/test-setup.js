// Controllable fake ResizeObserver for tests.
// global.__lastRO is updated each time a new instance is constructed, so tests
// can call .trigger() to simulate a resize notification and inspect .disconnected
// to verify dispose() cleaned up.  trigger() invokes the callback unconditionally
// (the real browser handles the post-disconnect case); tests that cover the
// disposed-flag race condition therefore rely on the production disposed check.
global.__lastRO = null;

global.ResizeObserver = class FakeResizeObserver {
  constructor(cb) {
    this._cb = cb;
    this.observed = [];
    this.disconnected = false;
    global.__lastRO = this;
  }
  observe(el) { this.observed.push(el); }
  unobserve(el) { this.observed = this.observed.filter((x) => x !== el); }
  disconnect() { this.disconnected = true; }
  trigger() { this._cb([]); }
};
