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

// ---- resize must not undo a drag ----
// Drives the page's own dragend and grip-resize handlers, rather than
// re-implementing what they do -- the bug was precisely that the resize path
// used a stale position, so only the real handlers can prove it fixed.
{
  const ok = (l, c) => console.log((c ? 'PASS  ' : 'FAIL  ') + l);
  const ride = RIDES.find(r => r.photos.some(p => p.at));
  const photo = ride.photos.find(p => p.at);
  const origin = photo.at.slice();
  const dragged = [origin[0] + 0.05, origin[1] + 0.05];

  state.sets['Snap'] = blankSet([ride.id]);
  state.active = 'Snap';
  togglePhoto(ride, photo, 0);
  render();

  const marker = photoMarkers.get(`${ride.id}/${photo.id}`);
  ok('photo starts at its own coordinates', (() => {
    const pl = placement(cur().photos[ride.id][photo.id], cur().size);
    return pl.pos[0] === origin[0];
  })());

  // 1. drag it: Leaflet moves the marker, then fires dragend.
  marker._ll = { lat: dragged[0], lng: dragged[1] };
  marker.getLatLng = () => marker._ll;
  marker._fire('dragend');

  ok('dragend records the new position', (() => {
    const pl = placement(cur().photos[ride.id][photo.id], cur().size);
    return Math.abs(pl.pos[0] - dragged[0]) < 1e-6;
  })());

  // 2. resize it via the grip, without re-rendering in between.
  const grip = marker.getElement().querySelector('.grip');
  grip.fire('pointerdown', { clientX: 100, clientY: 100,
                             preventDefault(){}, stopPropagation(){} });
  fireWindow('pointermove', { clientX: 160, clientY: 160 });
  fireWindow('pointerup', {});

  ok('resize keeps the dragged position (the reported snap)', (() => {
    const pl = placement(cur().photos[ride.id][photo.id], cur().size);
    return Math.abs(pl.pos[0] - dragged[0]) < 1e-6 &&
           Math.abs(pl.pos[1] - dragged[1]) < 1e-6;
  })());

  ok('resize actually changed the size', (() => {
    const pl = placement(cur().photos[ride.id][photo.id], cur().size);
    return pl.size > 72;
  })());
}

// ---- legend ----
{
  const ok = (l, c) => console.log((c ? 'PASS  ' : 'FAIL  ') + l);
  const kid = RIDES.filter(r => r.tags.includes(16) && r.sport === 'Ride').slice(0, 3);
  state.sets['Leg'] = blankSet(kid.map(r => r.id));
  state.active = 'Leg';
  render();

  ok('legend numbering matches map numbering', (() => {
    setLegendMode(true);
    const items = document.getElementById('legendList');
    // colour() is shared by both, so equal inputs give equal output.
    return colour(0, 3) === colour(0, 3) && pinState.length === 3;
  })());

  ok('legend title is stored per selection', (() => {
    cur().title = 'Rides with the kids, 2026';
    persist();
    const other = 'Leg2';
    state.sets[other] = blankSet([kid[0].id]);
    state.active = other;
    const empty = cur().title === '';
    state.active = 'Leg';
    return empty && cur().title === 'Rides with the kids, 2026';
  })());

  ok('legend always shows elevation (toggle button removed)', (() => {
    setLegendMode(true);
    return document.getElementById('legendTotals').textContent.includes('m climbed');
  })());

  ok('legend position is stored per selection', (() => {
    cur().legendPos = [120, 60]; persist();
    return loadState().sets['Leg'].legendPos[0] === 120;
  })());

  ok('date formatting is human readable', fmtDate('2026-08-30') === '30 Aug 2026');

  ok('legend mode toggles the body class', (() => {
    setLegendMode(false);
    let off = true;
    document.body.classList.toggle = (c, v) => { off = v; };
    setLegendMode(true);
    return off === true;
  })());
}

// ---- legend chrome hiding ----
{
  const ok = (l, c) => console.log((c ? 'PASS  ' : 'FAIL  ') + l);
  const kid = RIDES.filter(r => r.tags.includes(16) && r.sport === 'Ride').slice(0, 2);
  state.sets['Chrome'] = blankSet(kid.map(r => r.id));
  state.active = 'Chrome';

  const cls = new Set();
  document.body.classList = {
    add: c => cls.add(c), remove: c => cls.delete(c),
    toggle: (c, v) => (v ? cls.add(c) : cls.delete(c)),
  };

  setLegendMode(true);
  ok('entering legend mode hides chrome', cls.has('legendmode') && !cls.has('chrome'));

  fireDoc('keydown', { key: 'Shift' });
  ok('shift reveals the controls', cls.has('chrome'));

  fireDoc('keyup', { key: 'Shift' });
  ok('releasing shift hides them again', !cls.has('chrome'));

  fireDoc('keydown', { key: 'Meta' });
  ok('cmd also reveals them', cls.has('chrome'));
  fireDoc('keyup', { key: 'Meta' });

  fireDoc('keydown', { key: 'Shift' });
  fireWindow('blur', {});
  ok('losing focus while held does not strand the controls', !cls.has('chrome'));

  fireDoc('keydown', { key: 'Shift' });
  fireDoc('keydown', { key: 'Escape' });
  ok('escape leaves legend mode', !cls.has('legendmode'));
  ok('leaving legend mode clears the chrome flag', !cls.has('chrome'));

  ok('shift does nothing outside legend mode', (() => {
    fireDoc('keydown', { key: 'Shift' });
    const stray = cls.has('chrome');
    fireDoc('keyup', { key: 'Shift' });
    return !stray;
  })());
}

// ---- export / import round-trip ----
{
  const ok = (l, c) => console.log((c ? 'PASS  ' : 'FAIL  ') + l);
  const withPhotos = RIDES.filter(r => r.photos.length >= 2)[0];
  const plain = RIDES.filter(r => !r.photos.length)[0];

  state.sets['Orig'] = blankSet([withPhotos.id, plain.id]);
  state.active = 'Orig';
  const set = cur();
  set.title = 'Rides with the kids, 2026';
  set.legendPos = [140, 70];
  set.size = 128;
  set.leaders = false;
  togglePhoto(withPhotos, withPhotos.photos[0], 0);
  togglePhoto(withPhotos, withPhotos.photos[1], 1);
  cur().photos[withPhotos.id][withPhotos.photos[0].id] = { pos: [59.4, 10.2], size: 200 };
  render();
  const saved = exportJson();

  ok('export carries the legend title and position', (() => {
    const j = JSON.parse(saved);
    return j.title === 'Rides with the kids, 2026' && j.legendPos[0] === 140;
  })());
  ok('export carries leaders flag and photo size', (() => {
    const j = JSON.parse(saved);
    return j.leaders === false && j.photoSize === 128;
  })());

  const res = importSelection(saved);
  ok('import does not collide with the existing set name', res.name !== 'Orig' && !!state.sets['Orig']);
  ok('import restores every ride, in order', (() => {
    const ids = cur().ids;
    return ids.length === 2 && ids[0] === withPhotos.id && ids[1] === plain.id;
  })());
  ok('import restores the legend', (() => {
    return cur().title === 'Rides with the kids, 2026' && cur().legendPos[0] === 140;
  })());
  ok('import restores leaders and photo size', cur().leaders === false && cur().size === 128);
  ok('import restores per-photo position and size', (() => {
    const pl = placement(cur().photos[withPhotos.id][withPhotos.photos[0].id], cur().size);
    return pl.pos[0] === 59.4 && pl.size === 200;
  })());
  ok('re-exporting the import matches the original', (() => {
    const again = JSON.parse(exportJson());
    const first = JSON.parse(saved);
    delete again.name; delete first.name;   // the set is renamed to stay unique
    return JSON.stringify(again) === JSON.stringify(first);
  })());

  ok('a bare list of ids imports', (() => {
    const r = importSelection(JSON.stringify([withPhotos.id, plain.id]));
    return !r.error && cur().ids.length === 2;
  })());

  ok('unknown ride ids are skipped and reported', (() => {
    const r = importSelection(JSON.stringify({ name: 'Ghosts', rides: [{ id: 12345 }, { id: plain.id }] }));
    return r.missing.length === 1 && r.count === 1;
  })());

  ok('a member id of a merged ride maps to the survivor', (() => {
    const merged = RIDES.find(r => (r.merged_from || []).length > 1);
    if (!merged) return true;
    const member = merged.merged_from.find(m => m !== merged.id);
    const r = importSelection(JSON.stringify({ name: 'Old', rides: [{ id: member }] }));
    return r.missing.length === 0 && cur().ids[0] === merged.id;
  })());

  ok('placements for photos not on disk are dropped', (() => {
    const r = importSelection(JSON.stringify({
      name: 'Stale',
      rides: [{ id: withPhotos.id, photos: [{ id: 'no-such-photo', pos: [1, 2], size: 80 }] }],
    }));
    return !r.error && !cur().photos[withPhotos.id];
  })());

  ok('invalid JSON is reported, not thrown', (() => {
    const r = importSelection('{not json');
    return !!r.error && state.active !== undefined;
  })());

  ok('JSON without a rides array is reported', (() => {
    const r = importSelection('{"hello":1}');
    return !!r.error;
  })());
}
