// Two event sources with one output shape: {time, type, data}.
//   LiveStream   — Server-Sent Events from a running session
//   ReplayPlayer — a recorded trace, re-timed in the browser

export class LiveStream {
  constructor(url, onEvent, onStatus) {
    this.url = url;
    this.onEvent = onEvent;
    this.onStatus = onStatus || (() => {});
    this.source = null;
  }

  start() {
    // EventSource reconnects by itself and sends the last id it saw
    // (Last-Event-ID), so the server can resume without gaps or repeats.
    this.source = new EventSource(this.url);
    this.source.onopen = () => this.onStatus("open");
    this.source.onerror = () => this.onStatus("reconnecting");
    this.source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      this.onEvent({ time: event.ts, type: event.type, data: event.data || {} });
    };
  }

  stop() {
    if (this.source) this.source.close();
    this.source = null;
  }
}

// Long pauses in a recording are model latency; nobody wants to watch them.
const MAX_GAP_SECONDS = 1.2;

export class ReplayPlayer {
  /**
   * onEvent(event) may return true to ask the player to pause right after
   * that event — the guided tour uses this to stop and explain a step.
   */
  constructor(trace, onEvent, onChange) {
    this.events = trace.events || [];
    this.meta = trace.meta || {};
    this.onEvent = onEvent;
    this.onChange = onChange || (() => {});
    this.index = 0;
    this.speed = 1;
    this.playing = false;
    this.timer = null;
  }

  get total() { return this.events.length; }
  get progress() { return this.total ? this.index / this.total : 0; }
  get finished() { return this.index >= this.total; }

  play() {
    if (this.playing || this.finished) return;
    this.playing = true;
    this.onChange();
    this.step();
  }

  pause() {
    this.playing = false;
    clearTimeout(this.timer);
    this.onChange();
  }

  setSpeed(speed) {
    this.speed = speed;
    this.onChange();
  }

  // Emit everything that is left at once (the "skip to end" button).
  finish() {
    clearTimeout(this.timer);
    this.playing = false;
    while (!this.finished) this.onEvent(this.events[this.index++], { skipping: true });
    this.onChange();
  }

  // Jump to a fraction of the trace (used for stills of a run in progress).
  seek(fraction) {
    clearTimeout(this.timer);
    this.playing = false;
    const target = Math.round(Math.min(1, Math.max(0, fraction)) * this.total);
    while (this.index < target) this.onEvent(this.events[this.index++], { skipping: true });
    this.onChange();
  }

  step() {
    // Events closer together than a timer can resolve (token deltas) are
    // emitted in one batch; the batch is capped so the page can still paint.
    let batched = 0;
    while (this.playing && !this.finished) {
      const current = this.events[this.index++];
      const pauseHere = this.onEvent(current, { skipping: false }) === true;
      if (pauseHere) { this.playing = false; break; }
      const next = this.events[this.index];
      if (!next) break;
      const wait = Math.min(Math.max(0, next.t - current.t), MAX_GAP_SECONDS) / this.speed;
      if (wait >= 0.004 || ++batched >= 40) {
        this.timer = setTimeout(() => this.step(), wait * 1000);
        this.onChange();
        return;
      }
    }
    if (this.finished) this.playing = false;
    this.onChange();
  }

  destroy() {
    clearTimeout(this.timer);
    this.playing = false;
  }
}

export function normalizeTrace(trace) {
  return {
    meta: trace.meta || {},
    events: (trace.events || []).map((e) => ({ t: e.t, time: e.t, type: e.type, data: e.data || {} })),
  };
}
