// Minimal DOM/Leaflet stubs so the page script can be executed headlessly.
const store = {};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; },
};
global.storageDump = () => Object.keys(store);
const mkEl = () => ({
  value: '', textContent: '', innerHTML: '', className: '', src: '', title: '',
  style: {}, checked: false, scrollTop: 0, scrollLeft: 0, dataset: {},
  classList: { add(){}, remove(){}, toggle(){} },
  appendChild(){}, addEventListener(){}, removeEventListener(){},
  querySelector: () => null, getBoundingClientRect: () => ({top:0,right:0,height:0}),
  onclick: null, onchange: null, oninput: null,
  onmouseenter: null, onmouseleave: null, setAttribute(){}, getElement: () => null,
});
const els = {};
const docListeners = {};
global.document = {
  getElementById: id => (els[id] = els[id] || mkEl()),
  createElement: () => mkEl(),
  body: mkEl(),
  activeElement: null,
  addEventListener(k, fn) { (docListeners[k] = docListeners[k] || []).push(fn); },
};
global.fireDoc = (k, ev) => (docListeners[k] || []).slice().forEach(fn => fn(ev));
global.navigator = { clipboard: { writeText(){} } };
// Served page by default, so the fetch-photos controls are exercised. Tests
// that care about the file:// case override this before loading the page.
global.location = global.location || { protocol: 'http:' };
// Records calls instead of hitting the network; tests drive the response.
global.fetchCalls = [];
global.fetchResponse = { ok: true, scanned: 0, downloaded: 0, videos: 0, rides: {} };
global.fetch = (url, opts) => {
  fetchCalls.push({ url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ json: () => Promise.resolve(fetchResponse) });
};
global.alert = () => {}; global.confirm = () => true; global.prompt = () => 'X';
global.Blob = class {}; global.URL = { createObjectURL: () => '', revokeObjectURL(){} };
global.setTimeout = (fn) => { if (typeof fn === 'function') fn(); };
const winListeners = {};
global.window = {
  innerHeight: 900,
  addEventListener(k, fn) { (winListeners[k] = winListeners[k] || []).push(fn); },
  removeEventListener(k, fn) {
    if (winListeners[k]) winListeners[k] = winListeners[k].filter(f => f !== fn);
  },
};
// Drive a real pointer gesture against whatever the page registered.
global.fireWindow = (k, ev) => (winListeners[k] || []).slice().forEach(fn => fn(ev));

const gripEl = () => { const g = mkEl(); g.listeners = {};
  g.addEventListener = (k, fn) => { (g.listeners[k] = g.listeners[k] || []).push(fn); };
  g.fire = (k, ev) => (g.listeners[k] || []).slice().forEach(fn => fn(ev));
  return g; };
const layer = () => ({ addTo(){ return this; }, clearLayers(){}, bindPopup(){ return this; }, setLatLngs(){},
  setLatLng(ll){ this._ll = ll; return this; },
  on(k, fn) { (this._h = this._h || {}); (this._h[k] = this._h[k] || []).push(fn); return this; },
  _fire(k, ev) { ((this._h || {})[k] || []).slice().forEach(fn => fn(ev)); return this; },
  setStyle(){}, bringToFront(){},
  getElement(){ if (!this._el) { this._el = mkEl(); this._el._grip = gripEl();
      this._el.querySelector = sel => sel === '.grip' ? this._el._grip : null; } return this._el; },
  getLatLng: () => ({ lat: 1, lng: 2 }) });
global.L = {
  // Faithful to Leaflet: projecting before a view is set throws, and getZoom()
  // returns undefined rather than throwing.
  map: () => ({
    _loaded: false,
    addTo(){}, on(){},
    setView(c, z){ this._loaded = true;
      this._c = { lat: Array.isArray(c) ? c[0] : c.lat, lng: Array.isArray(c) ? c[1] : c.lng };
      if (z !== undefined) this._z = z; return this; },
    fitBounds(b, o){ if (b && b._empty) throw new Error('Bounds are not valid.');
      this._loaded = true; this._c = { lat: 59.9, lng: 10.7 }; this._z = 12;
      this._lastFitOpts = o; return this; },
    getCenter(){ this._check(); return this._c || { lat: 59.9, lng: 10.7 }; },
    getZoom(){ return this._loaded ? (this._z === undefined ? 12 : this._z) : undefined; },
    // Real invalidateSize pans when the container changes; if the page ever
    // calls it on a mode switch, this makes that visible as a moved centre.
    invalidateSize(){ this._c = { lat: this._c.lat + 0.01, lng: this._c.lng + 0.01 }; },
    dragging: { enable(){}, disable(){} },
    _check(){ if (!this._loaded) throw new Error('Set map center and zoom first.'); },
    latLngToLayerPoint(ll){ this._check();
      return { x: (ll.lng ?? ll[1]) * 100000, y: -(ll.lat ?? ll[0]) * 100000 }; },
    layerPointToLatLng(p){ this._check(); return { lat: -p.y / 100000, lng: p.x / 100000 }; },
  }),
  tileLayer: () => layer(), layerGroup: () => layer(), polyline: () => layer(),
  marker: () => layer(), divIcon: () => ({}), circleMarker: () => layer(),
  latLng: (a) => ({ lat: Array.isArray(a) ? a[0] : a.lat, lng: Array.isArray(a) ? a[1] : a.lng }),
  control: { layers: () => ({ addTo(){} }), zoom: () => ({ addTo(){} }) },
  DomEvent: { disableClickPropagation(){}, disableScrollPropagation(){} },
  latLngBounds: () => ({ _empty: true, extend(){ this._empty = false; },
                         isValid(){ return !this._empty; } }),
};
