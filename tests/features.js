// Behavioural checks for the built page, run against the stubs in harness.js.
// Each suite is block-scoped so they can share the same page instance.

// ---- photos: placement, sizing, persistence ----
{
  const ok = (l, c) => console.log((c ? 'PASS  ' : 'FAIL  ') + l);
  const kid = RIDES.filter(r => r.tags.includes(16) && r.sport === 'Ride');
  const wp = kid.find(r => r.photos.filter(p => p.at).length > 1);
  const noGeo = RIDES.flatMap(r => r.photos).find(p => !p.at);

  state.sets['T'] = blankSet([wp.id]); state.active = 'T';

  ok('new placement defaults to the photo\'s own coordinates', (() => {
    const ph = wp.photos.find(p => p.at);
    togglePhoto(wp, ph, 0);
    const pl = placement(cur().photos[wp.id][ph.id], cur().size);
    return JSON.stringify(pl.pos) === JSON.stringify(ph.at);
  })());

  ok('placement carries a size', (() => {
    const ph = wp.photos.find(p => p.at);
    return placement(cur().photos[wp.id][ph.id], cur().size).size === cur().size;
  })());

  ok('legacy bare [lat,lng] placement still reads', (() => {
    const pl = placement([59.9, 10.7], 72);
    return pl.pos[0] === 59.9 && pl.size === 72;
  })());

  ok('photoBox preserves aspect ratio (portrait)', (() => {
    const [w, h] = photoBox({ w: 1536, h: 2048 }, 100);
    return h === 100 && w === 75;
  })());
  ok('photoBox preserves aspect ratio (landscape)', (() => {
    const [w, h] = photoBox({ w: 2048, h: 1536 }, 100);
    return w === 100 && h === 75;
  })());
  ok('photoBox tolerates missing dimensions', (() => {
    const [w, h] = photoBox({}, 80);
    return w === 80 && h === 60;
  })());

  ok('leaders default on and toggle', (() => {
    const before = cur().leaders;
    cur().leaders = !before;
    return before === true && cur().leaders === false;
  })());
  cur().leaders = true;

  ok('global size resizes every placed photo', (() => {
    const ph2 = wp.photos.filter(p => p.at)[1];
    togglePhoto(wp, ph2, 1);
    const size = 160;
    cur().size = size;
    for (const fr of Object.values(cur().photos))
      for (const id of Object.keys(fr)) fr[id] = { pos: placement(fr[id], size).pos, size };
    return Object.values(cur().photos[wp.id]).every(v => v.size === 160);
  })());

  ok('a photo without GPS still places (just no line)', (() => {
    if (!noGeo) return true;
    const ride = RIDES.find(r => r.photos.includes(noGeo));
    state.sets['N'] = blankSet([ride.id]); state.active = 'N';
    togglePhoto(ride, noGeo, 0);
    const pl = placement(cur().photos[ride.id][noGeo.id], cur().size);
    return Array.isArray(pl.pos) && noGeo.at === null;
  })());

  state.active = 'T';
  ok('export includes size and taken_at', (() => {
    const j = JSON.parse(exportJson());
    const p = j.rides[0].photos[0];
    return p.size === 160 && Array.isArray(p.pos) && Array.isArray(p.taken_at);
  })());

  ok('leaders + size survive a reload', (() => {
    cur().leaders = false; cur().size = 96; persist();
    const s2 = loadState();
    return s2.sets['T'].leaders === false && s2.sets['T'].size === 96;
  })());
}

// ---- resize grip and pin de-collision ----
{
  const ok = (l, c) => console.log((c ? 'PASS  ' : 'FAIL  ') + l);
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

  // ---- 1. resize: the grip must stop every event Leaflet starts a drag on ----
  const kid = RIDES.filter(r => r.tags.includes(16) && r.sport === 'Ride');
  const wp = kid.find(r => r.photos.filter(p => p.at).length > 1);
  state.sets['R'] = blankSet([wp.id]); state.active = 'R';
  togglePhoto(wp, wp.photos.find(p => p.at), 0);
  render();
  const pm = photoMarkers.get(`${wp.id}/${wp.photos.find(p => p.at).id}`);
  const grip = pm.getElement().querySelector('.grip');
  ok('grip intercepts mousedown (what Leaflet drags on)', !!(grip.listeners && grip.listeners['mousedown']));
  ok('grip intercepts touchstart', !!(grip.listeners && grip.listeners['touchstart']));
  ok('grip still handles pointerdown for the resize itself', !!(grip.listeners && grip.listeners['pointerdown']));
  ok('mousedown handler stops propagation', (() => {
    let stopped = false, prevented = false;
    grip.listeners['mousedown'][0]({ stopPropagation: () => { stopped = true; },
                                     preventDefault: () => { prevented = true; } });
    return stopped && prevented;
  })());

  // ---- 2. coincident pins get pushed apart ----------------------------------
  ok('two identical turnarounds are separated', (() => {
    const a = JSON.parse(JSON.stringify(kid[0]));
    const b = JSON.parse(JSON.stringify(kid[1]));
    a.id = 900001; b.id = 900002;
    b.label_at = a.label_at.slice();          // exactly the same return point
    byId.set(a.id, a); byId.set(b.id, b);
    RIDES.push(a, b);
    state.sets['P'] = blankSet([a.id, b.id]); state.active = 'P';
    render();
    const pa = map.latLngToLayerPoint(pinState[0].marker._ll);
    const pb = map.latLngToLayerPoint(pinState[1].marker._ll);
    return dist(pa, pb) >= PIN_MIN_PX - 0.5;
  })());

  ok('separated pins keep their true positions recorded', (() => {
    return pinState.every(s => Array.isArray(s.truePos));
  })());

  ok('well-separated pins are left where they are', (() => {
    const far = kid.filter(r => r.far_km > 4).slice(0, 3);
    state.sets['F'] = blankSet(far.map(r => r.id)); state.active = 'F';
    render();
    return pinState.every((s, i) => {
      const at = map.latLngToLayerPoint(s.marker._ll);
      const home = map.latLngToLayerPoint(L.latLng(s.truePos));
      return dist(at, home) < 0.5;
    });
  })());

  ok('a whole cluster of coincident pins all separate', (() => {
    const clones = [];
    for (let i = 0; i < 5; i++) {
      const c = JSON.parse(JSON.stringify(kid[2]));
      c.id = 910000 + i; c.label_at = kid[2].label_at.slice();
      byId.set(c.id, c); RIDES.push(c); clones.push(c.id);
    }
    state.sets['C'] = blankSet(clones); state.active = 'C';
    render();
    const pts = pinState.map(s => map.latLngToLayerPoint(s.marker._ll));
    let worst = Infinity;
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++) worst = Math.min(worst, dist(pts[i], pts[j]));
    return worst >= PIN_MIN_PX - 1;
  })());
}
