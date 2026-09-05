// Minimal DOM/Leaflet stubs so the page script can be executed headlessly.
const store = {};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
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
global.document = {
  getElementById: id => (els[id] = els[id] || mkEl()),
  createElement: () => mkEl(),
  body: mkEl(),
  activeElement: null,
};
global.navigator = { clipboard: { writeText(){} } };
global.alert = () => {}; global.confirm = () => true; global.prompt = () => 'X';
global.Blob = class {}; global.URL = { createObjectURL: () => '', revokeObjectURL(){} };
global.setTimeout = () => {};
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
    setView(){ this._loaded = true; return this; },
    fitBounds(b){ if (b && b._empty) throw new Error('Bounds are not valid.');
                  this._loaded = true; return this; },
    getCenter(){ this._check(); return {lat:1,lng:2}; },
    getZoom(){ return this._loaded ? 12 : undefined; },
    dragging: { enable(){}, disable(){} },
    invalidateSize(){},
    _check(){ if (!this._loaded) throw new Error('Set map center and zoom first.'); },
    latLngToLayerPoint(ll){ this._check();
      return { x: (ll.lng ?? ll[1]) * 100000, y: -(ll.lat ?? ll[0]) * 100000 }; },
    layerPointToLatLng(p){ this._check(); return { lat: -p.y / 100000, lng: p.x / 100000 }; },
  }),
  tileLayer: () => layer(), layerGroup: () => layer(), polyline: () => layer(),
  marker: () => layer(), divIcon: () => ({}), circleMarker: () => layer(),
  latLng: (a) => ({ lat: Array.isArray(a) ? a[0] : a.lat, lng: Array.isArray(a) ? a[1] : a.lng }),
  control: { layers: () => ({ addTo(){} }) },
  latLngBounds: () => ({ _empty: true, extend(){ this._empty = false; },
                         isValid(){ return !this._empty; } }),
};
