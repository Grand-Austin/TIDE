// Copernicus sea state on the passage map, the way Copernicus MyOcean draws it: significant wave height as a colour
// field and 10 m wind as drifting streamlines, both following scene time. Data: web/game/fields/<storm>.json from
// pipeline/export_fields.py (the storm's wave grid every 3 h, wind interpolated onto it). Replays only: in live play
// the full field would show the hidden escape region, so the map keeps to released forecasts there.

const HS_MAX = 10; // m at the top of the colour scale
// magma, as on MyOcean's wave height layer: dark calm sea to pale yellow at HS_MAX
const MAGMA = [[0, 0, 0, 4], [0.2, 59, 15, 112], [0.45, 140, 41, 129], [0.65, 222, 73, 104], [0.85, 254, 159, 109], [1, 252, 253, 191]];
const UPSAMPLE = 8; // canvas pixels per grid cell, smoothed bilinearly
const PARTICLES = 260, PX2_PER_PARTICLE = 900, MAX_AGE = 80, PX_PER_MS = 0.12; // a 20 m/s wind moves a streak about 2.4 px a frame
const REDRAW_STEP = 0.05; // of a 3 h step, about 9 min of scene time between colour-field redraws

export const HS_GRADIENT = `linear-gradient(90deg, ${MAGMA.map(([f, r, g, b]) => `rgb(${r},${g},${b}) ${f * 100}%`).join(", ")})`;

export async function loadField(stormId) {
  if (!stormId) return null;
  try {
    const r = await fetch(`./fields/${stormId}.json`);
    if (!r.ok) return null;
    const f = await r.json();
    f.ms = f.times.map(Date.parse);
    return f;
  } catch {
    return null;
  }
}

function magma(x) {
  x = Math.min(1, Math.max(0, x));
  let k = 1;
  while (MAGMA[k][0] < x) k++;
  const [f0, ...a] = MAGMA[k - 1], [f1, ...b] = MAGMA[k], w = (x - f0) / (f1 - f0);
  return a.map((v, i) => Math.round(v + (b[i] - v) * w));
}

// Bilinear value of one variable at fractional grid position (i along lon, j along lat) and time index k;
// NaN when any surrounding cell is land.
function sample(field, name, k, i, j) {
  const nx = field.lon.length, ny = field.lat.length;
  if (i < 0 || j < 0 || i > nx - 1 || j > ny - 1) return NaN;
  const k0 = Math.floor(k), k1 = Math.min(k0 + 1, field.ms.length - 1), wk = k - k0;
  const i0 = Math.min(Math.floor(i), nx - 2), j0 = Math.min(Math.floor(j), ny - 2), wi = i - i0, wj = j - j0;
  let sum = 0;
  for (const [kk, fk] of [[k0, 1 - wk], [k1, wk]]) {
    if (!fk) continue;
    const g = field[name][kk];
    const c = [g[j0 * nx + i0], g[j0 * nx + i0 + 1], g[(j0 + 1) * nx + i0], g[(j0 + 1) * nx + i0 + 1]];
    if (c.some((v) => v === null)) return NaN;
    sum += fk * ((c[0] * (1 - wi) + c[1] * wi) * (1 - wj) + (c[2] * (1 - wi) + c[3] * wi) * wj);
  }
  return sum / 10;
}

export function addWeather(map, field, beforeId) {
  const nx = field.lon.length, ny = field.lat.length;
  const [lon0, lat0] = [field.lon[0], field.lat[0]];
  const dlon = (field.lon.at(-1) - lon0) / (nx - 1), dlat = (field.lat.at(-1) - lat0) / (ny - 1);
  const grid = (lng, lat) => [(lng - lon0) / dlon, (lat - lat0) / dlat];
  // the image covers grid centres from the first to the last; north at the top whatever the latitude order
  const [w, e] = [lon0, field.lon.at(-1)], [s, n] = [Math.min(lat0, field.lat.at(-1)), Math.max(lat0, field.lat.at(-1))];
  const W = (nx - 1) * UPSAMPLE, H = (ny - 1) * UPSAMPLE;
  const image = document.createElement("canvas");
  [image.width, image.height] = [W, H];
  const ictx = image.getContext("2d");
  let k = 0, drawnK = -1;

  const paint = () => {
    const px = ictx.createImageData(W, H);
    for (let y = 0; y < H; y++) {
      const lat = n - ((n - s) * (y + 0.5)) / H;
      for (let x = 0; x < W; x++) {
        const [i, j] = grid(w + ((e - w) * (x + 0.5)) / W, lat), hs = sample(field, "hs", k, i, j), o = 4 * (y * W + x);
        if (Number.isNaN(hs)) continue; // land and coast stay the basemap's
        const [r, g, b] = magma(hs / HS_MAX);
        [px.data[o], px.data[o + 1], px.data[o + 2], px.data[o + 3]] = [r, g, b, 255];
      }
    }
    ictx.putImageData(px, 0, 0);
    map.getSource("weather").updateImage({ url: image.toDataURL(), coordinates: [[w, n], [e, n], [e, s], [w, s]] });
    drawnK = k;
  };

  map.addSource("weather", { type: "image", url: image.toDataURL(), coordinates: [[w, n], [e, n], [e, s], [w, s]] }); // credited in the map legend
  map.addLayer({ id: "weather", type: "raster", source: "weather", paint: { "raster-opacity": 0.72, "raster-fade-duration": 0 } }, beforeId);

  // Wind streamlines: particles drift with the local wind in screen space and leave fading trails.
  const trails = document.createElement("canvas");
  trails.className = "wind-trails";
  trails.style.cssText = "position:absolute;inset:0;pointer-events:none";
  map.getCanvasContainer().appendChild(trails);
  const tctx = trails.getContext("2d");
  const spawn = (p) => { // anywhere in view that has data
    const v = map.getBounds(), x0 = Math.max(w, v.getWest()), x1 = Math.min(e, v.getEast());
    const y0 = Math.max(s, v.getSouth()), y1 = Math.min(n, v.getNorth());
    p.lng = x0 < x1 ? x0 + Math.random() * (x1 - x0) : w;
    p.lat = y0 < y1 ? y0 + Math.random() * (y1 - y0) : s;
    p.age = Math.floor(Math.random() * MAX_AGE);
    return p;
  };
  const particles = Array.from({ length: PARTICLES }, () => spawn({}));
  const resize = () => {
    const c = map.getCanvas();
    [trails.width, trails.height] = [c.width, c.height];
    [trails.style.width, trails.style.height] = [c.style.width, c.style.height];
  };
  resize();
  map.on("resize", resize);
  map.on("move", () => tctx.clearRect(0, 0, trails.width, trails.height));
  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min(50, now - last), ratio = trails.width / trails.clientWidth || 1;
    last = now;
    tctx.globalCompositeOperation = "destination-out";
    tctx.fillStyle = "rgba(0,0,0,0.12)";
    tctx.fillRect(0, 0, trails.width, trails.height);
    tctx.globalCompositeOperation = "source-over";
    tctx.strokeStyle = "rgba(235,242,250,0.5)";
    tctx.lineWidth = ratio;
    tctx.beginPath();
    // fewer streaks when the field is small on screen, so a zoomed-out view is not a white smear
    const a0 = map.project([w, n]), a1 = map.project([e, s]);
    const shown = Math.min(trails.clientWidth, Math.abs(a1.x - a0.x)) * Math.min(trails.clientHeight, Math.abs(a1.y - a0.y));
    for (const p of particles.slice(0, Math.min(PARTICLES, Math.ceil(shown / PX2_PER_PARTICLE)))) {
      const [i, j] = grid(p.lng, p.lat), u = sample(field, "wind_e", k, i, j), v = sample(field, "wind_n", k, i, j);
      if (Number.isNaN(u) || ++p.age > MAX_AGE) {
        spawn(p).age = 0;
        continue;
      }
      const a = map.project([p.lng, p.lat]), b = { x: a.x + u * PX_PER_MS * dt / 16, y: a.y - v * PX_PER_MS * dt / 16 };
      const next = map.unproject([b.x, b.y]);
      [p.lng, p.lat] = [next.lng, next.lat];
      tctx.moveTo(a.x * ratio, a.y * ratio);
      tctx.lineTo(b.x * ratio, b.y * ratio);
    }
    tctx.stroke();
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);

  // Scene time in ms since the epoch; the colour field follows in REDRAW_STEP steps between the 3-hourly fields.
  return (ms) => {
    const T = field.ms, j = Math.max(0, Math.min(T.length - 1.001, (() => {
      let a = 0;
      while (a < T.length - 2 && T[a + 1] <= ms) a++;
      return a + (ms - T[a]) / (T[a + 1] - T[a]);
    })()));
    k = Math.max(0, j);
    if (drawnK < 0 || Math.abs(k - drawnK) >= REDRAW_STEP) paint();
  };
}
