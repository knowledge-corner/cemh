/*
 * Growth chart rendering.
 *
 * Draws the published percentile curves as thin grey lines with the patient's
 * own measurements as a heavier teal line on top — the convention a paediatric
 * endocrinologist already reads charts by.
 *
 * Called explicitly after the growth tab is swapped in by HTMX, so it cannot
 * rely on DOMContentLoaded.
 */

/* global Chart */

/*
 * "Nice" round numbers for axis steps (1, 2, 2.5, 5, 10 x 10^n) — the same
 * rule printed growth charts use so gridlines land on numbers a clinician
 * would actually read off, not on whatever the data's raw range happens to be.
 */
function niceStep(rawStep) {
  if (!rawStep || rawStep <= 0) return 1;
  const exponent = Math.floor(Math.log10(rawStep));
  const fraction = rawStep / Math.pow(10, exponent);
  let niceFraction;
  if (fraction <= 1) niceFraction = 1;
  else if (fraction <= 2) niceFraction = 2;
  else if (fraction <= 5) niceFraction = 5;
  else niceFraction = 10;
  return niceFraction * Math.pow(10, exponent);
}

/*
 * Height, weight and BMI are the three charts a printed WHO/IAP sheet draws
 * as dense graph paper — a gridline every whole unit, a number only every
 * fifth one. Head circumference keeps the old auto-scaled step, since
 * nothing asked for it to match the paper chart too.
 */
function isDenseGridIndicator(indicator) {
  return indicator === 'lhfa' || indicator === 'wfa' || indicator === 'bmifa';
}

/*
 * Explicit min/max/step for the value axis, shared by the left and right
 * y-scales so their tick rows line up — the printed WHO/IAP charts label
 * both edges of the same dense grid rather than leaving the right side bare.
 */
function computeYDomain(data) {
  const values = [];
  data.curves.forEach(function (c) { c.points.forEach(function (p) { values.push(p.value); }); });
  (data.cutoffs || []).forEach(function (c) { c.points.forEach(function (p) { values.push(p.value); }); });
  data.points.forEach(function (p) { values.push(p.value); });
  (data.bone_age_points || []).forEach(function (p) { values.push(p.value); });
  if (data.mid_parental) {
    values.push(data.mid_parental.low, data.mid_parental.high, data.mid_parental.target);
  }
  if (!values.length) return null;

  const rawMin = Math.min.apply(null, values);
  const rawMax = Math.max.apply(null, values);

  if (isDenseGridIndicator(data.indicator)) {
    const labelStep = 5;
    return {
      min: Math.max(0, Math.floor((rawMin - labelStep) / labelStep) * labelStep),
      max: Math.ceil((rawMax + labelStep) / labelStep) * labelStep,
      step: 1,
      labelStep: labelStep
    };
  }

  const step = niceStep((rawMax - rawMin || 1) / 10);
  return {
    min: Math.max(0, Math.floor(rawMin / step) * step - step),
    max: Math.ceil(rawMax / step) * step + step,
    step: step
  };
}

/*
 * Tick options shared by the left and right value axes. Plain "color/font"
 * for the ordinary auto-scaled charts; for the dense-grid ones a gridline is
 * still drawn every unit (stepSize handles that) but the label text is
 * blanked except on every fifth one, the way the printed chart only writes
 * a number at every fifth line.
 */
function buildValueAxisTicks(yDomain, dense, muted) {
  const ticks = { color: muted, font: { size: 10 } };
  if (yDomain) ticks.stepSize = yDomain.step;
  if (dense && yDomain) {
    // Chart.js's autoSkip thins out ticks - gridline and label together -
    // whenever it judges the labels would crowd. That is exactly what would
    // happen here: it would quietly widen the 1-unit grid back out, since it
    // has no idea most of these labels are blank on purpose. Off, so every
    // integer keeps its gridline regardless of how few of them carry text.
    ticks.autoSkip = false;
    ticks.callback = function (value) {
      const rounded = Math.round(value);
      return rounded % yDomain.labelStep === 0 ? rounded : '';
    };
  }
  return ticks;
}

/*
 * How many months apart the age gridlines fall — finer for the infant/WHO
 * charts, yearly for the older IAP range, so the grid reads as dense graph
 * paper at any age span rather than Chart.js's sparser auto-picked ticks.
 */
function computeXStep(data) {
  const months = [];
  data.curves.forEach(function (c) { c.points.forEach(function (p) { months.push(p.month); }); });
  if (!months.length) return 12;
  const maxMonth = Math.max.apply(null, months);
  if (maxMonth > 60) return 12;
  if (maxMonth > 24) return 6;
  return 2;
}

/*
 * Printed growth charts write the percentile number at the end of its own
 * curve (97, 90, 75 … ) instead of a legend — this plugin draws that number
 * beside the last point of every "P<n>" dataset, in the curve's own colour.
 */
const growthPercentileEndLabelsPlugin = {
  id: 'growthPercentileEndLabels',
  afterDatasetsDraw: function (chart) {
    const ctx = chart.ctx;
    chart.data.datasets.forEach(function (dataset, i) {
      const match = /^P(\d+)$/.exec(dataset.label || '');
      if (!match) return;
      const meta = chart.getDatasetMeta(i);
      if (!meta || meta.hidden || !meta.data.length) return;
      const last = meta.data[meta.data.length - 1];
      if (!last) return;
      ctx.save();
      ctx.font = '600 10px sans-serif';
      ctx.fillStyle = dataset.borderColor;
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      ctx.fillText(match[1], last.x + 4, last.y);
      ctx.restore();
    });
  }
};

function growthThemeColour(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value ? value.trim() : fallback;
}

function growthChartColours() {
  return {
    teal: growthThemeColour('--brand-teal', '#17a398'),
    dark: growthThemeColour('--brand-dark', '#414e54'),
    muted: growthThemeColour('--text-faint', '#93a3a8'),
    border: growthThemeColour('--border', '#d8e2e2'),
    warning: growthThemeColour('--warning', '#c77700'),
    danger: growthThemeColour('--danger', '#c0392b'),
    info: growthThemeColour('--info', '#10769f'),
    // The warm accent, reserved for exactly this — see theme.css's note that
    // it exists for "the growth tab" among a few other highlights.
    accent: growthThemeColour('--brand-accent', '#ff6b4a')
  };
}

/*
 * The dataset list and options object for one chart's data — shared between
 * the small inline chart and its zoomed-up modal twin, so the two can never
 * quietly drift apart on what they draw.
 */
function buildGrowthChartConfig(data, colours) {
  const teal = colours.teal, dark = colours.dark, muted = colours.muted,
    border = colours.border, warning = colours.warning, danger = colours.danger,
    info = colours.info, accent = colours.accent;

  // Reference curves first so the patient's own line draws over them.
  const datasets = data.curves.map(function (curve) {
    return {
      label: 'P' + curve.percentile,
      data: curve.points.map(function (p) { return { x: p.month, y: p.value }; }),
      // The median is drawn solid and the outer centiles dashed, so the
      // reference family reads as context without competing with the
      // patient's own line.
      borderColor: curve.percentile === 50 ? dark : muted,
      borderWidth: curve.percentile === 50 ? 1.5 : 1,
      borderDash: curve.percentile === 50 ? [] : [4, 3],
      pointRadius: 0,
      fill: false,
      tension: 0.35,
      order: 2
    };
  });

  // Non-centile reference lines: the IAP BMI chart's adult-equivalent
  // cut-offs, in warning colours since they mark overweight and obesity.
  // The height chart's mid-parental target is handled separately below —
  // it is a point with a range, not a line across the whole chart.
  (data.cutoffs || []).forEach(function (line) {
    let color = warning;
    if (line.key === 'Eq27') color = danger;
    datasets.push({
      label: line.label,
      data: line.points.map(function (p) { return { x: p.month, y: p.value }; }),
      borderColor: color,
      borderWidth: 1.5,
      borderDash: [7, 3],
      pointRadius: 0,
      fill: false,
      tension: 0.35,
      order: 2
    });
  });

  datasets.push({
    label: 'This patient',
    data: data.points.map(function (p) {
      return {
        x: p.month, y: p.value, date: p.date,
        // Whichever of these the reference could supply; the tooltip shows
        // the exact centile for WHO and CDC, the band for IAP.
        percentile: p.percentile, band: p.band_label, sds: p.sds
      };
    }),
    borderColor: teal,
    backgroundColor: teal,
    borderWidth: 2.5,
    pointRadius: 4,
    pointHoverRadius: 6,
    fill: false,
    tension: 0.2,
    order: 1
  });

  // The mid-parental target: a short vertical bracket at the chart's right
  // edge spanning the ± range either side of it, with the target itself
  // marked as a single dot rather than a line — a dashed line the full
  // width of the chart read as a threshold, when it is one target value.
  if (data.mid_parental) {
    const mp = data.mid_parental;
    const lastPoint = data.points.length ? data.points[data.points.length - 1] : null;

    datasets.push({
      label: 'Mid-parental range',
      data: [{ x: mp.month, y: mp.low }, { x: mp.month, y: mp.high }],
      borderColor: info,
      borderWidth: 3,
      pointRadius: 0,
      fill: false,
      order: 2
    });

    datasets.push({
      label: 'Mid-parental target',
      data: [{ x: mp.month, y: mp.target }],
      borderColor: info,
      backgroundColor: info,
      showLine: false,
      pointStyle: 'circle',
      pointRadius: 5,
      pointHoverRadius: 7,
      order: 0
    });

    // A dashed guide from the child's latest height across to the target's
    // x position, so the gap between where the child is and the
    // mid-parental range is something to look at, not something to work out.
    if (lastPoint) {
      datasets.push({
        label: '',
        _isGuide: true,
        data: [
          { x: lastPoint.month, y: lastPoint.value },
          { x: mp.month, y: lastPoint.value }
        ],
        borderColor: muted,
        borderWidth: 1,
        borderDash: [3, 3],
        pointRadius: 0,
        fill: false,
        order: 2
      });
    }
  }

  // A second reading of the same height, positioned at the bone-age x
  // rather than the chronological one — points only, never a line, since
  // there is nothing between two skeletal-age readings to connect.
  if (data.bone_age_points && data.bone_age_points.length) {
    datasets.push({
      label: 'Bone age',
      data: data.bone_age_points.map(function (p) {
        return {
          x: p.month, y: p.value, date: p.date,
          chronologicalMonth: p.chronological_month,
          boneAgeYears: p.bone_age_years
        };
      }),
      borderColor: accent,
      backgroundColor: accent,
      showLine: false,
      pointStyle: 'rectRot',
      pointRadius: 5,
      pointHoverRadius: 7,
      order: 0
    });
  }

  const yDomain = computeYDomain(data);
  const xStep = computeXStep(data);

  return {
    type: 'line',
    data: { datasets: datasets },
    plugins: [growthPercentileEndLabelsPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      // Room on the right for the percentile numbers the plugin above draws
      // past the last point of each curve.
      layout: { padding: { right: 22 } },
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Age (years)', color: muted, font: { size: 11 } },
          ticks: {
            color: muted,
            font: { size: 10 },
            stepSize: xStep,
            // Tables are keyed in months; clinicians read charts in years.
            callback: function (value) { return (value / 12).toFixed(value % 12 ? 1 : 0); }
          },
          grid: { color: border }
        },
        y: {
          position: 'left',
          title: { display: true, text: data.unit, color: muted, font: { size: 11 } },
          ticks: buildValueAxisTicks(yDomain, isDenseGridIndicator(data.indicator), muted),
          grid: { color: border },
          min: yDomain ? yDomain.min : undefined,
          max: yDomain ? yDomain.max : undefined
        },
        // Mirrors the left axis exactly — same explicit min/max/step — so a
        // value can be read off whichever edge is closer, the way the
        // printed chart labels both sides of one shared grid.
        y2: {
          position: 'right',
          title: { display: true, text: data.unit, color: muted, font: { size: 11 } },
          ticks: buildValueAxisTicks(yDomain, isDenseGridIndicator(data.indicator), muted),
          grid: { drawOnChartArea: false },
          min: yDomain ? yDomain.min : undefined,
          max: yDomain ? yDomain.max : undefined
        }
      },
      plugins: {
        legend: {
          display: true,
          labels: {
            color: dark,
            boxWidth: 18,
            font: { size: 10 },
            // The patient's line, the named cut-offs, bone age and the
            // mid-parental target get a legend entry; seven anonymous
            // percentile labels would swamp a small chart.
            filter: function (item) {
              return item.text === 'This patient' || item.text === 'Bone age' ||
                /^(Overweight|Obesity|Mid-parental)/.test(item.text);
            }
          }
        },
        tooltip: {
          // The dashed guide line connecting the child's latest height to
          // the mid-parental marker is a visual aid, not a data point — it
          // repeats a height already shown by "This patient" itself.
          filter: function (item) { return !item.dataset._isGuide; },
          callbacks: {
            title: function (items) {
              const raw = items[0].raw;
              if (raw && raw.date) return raw.date;
              return (items[0].parsed.x / 12).toFixed(1) + ' years';
            },
            label: function (item) {
              const raw = item.raw;
              // A bone-age point is not a second measurement — it is the
              // same height read at a different x, so its own text rather
              // than the generic "label: value unit" line, which would
              // otherwise repeat the height with no explanation of why it
              // is not where the chronological point is.
              if (raw && raw.boneAgeYears !== undefined) {
                return 'Bone age ' + raw.boneAgeYears + 'y (chronological ' +
                  (raw.chronologicalMonth / 12).toFixed(1) + 'y) — height ' +
                  item.parsed.y + ' ' + data.unit;
              }
              let text = item.dataset.label + ': ' + item.parsed.y + ' ' + data.unit;
              if (!raw) return text;
              // An exact centile where the reference supports one, otherwise
              // the band it was read off the printed table as.
              if (raw.percentile !== null && raw.percentile !== undefined) {
                text += '  (' + raw.percentile + 'th centile)';
              } else if (raw.band) {
                text += '  (' + raw.band;
                text += raw.sds !== null && raw.sds !== undefined ? ', SDS ' + raw.sds + ')' : ')';
              }
              return text;
            }
          }
        }
      }
    }
  };
}

function renderOneGrowthChart(canvas, data, colours) {
  // Re-rendering must not stack a second chart on the same canvas.
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  return new Chart(canvas, buildGrowthChartConfig(data, colours));
}

function renderGrowthCharts() {
  const container = document.getElementById('growth-charts');
  if (!container || typeof Chart === 'undefined') return;

  const colours = growthChartColours();

  container.querySelectorAll('canvas[data-chart]').forEach(function (canvas) {
    let data;
    try {
      data = JSON.parse(canvas.dataset.chart);
    } catch (err) {
      return;
    }
    renderOneGrowthChart(canvas, data, colours);
  });
}

/*
 * The click-to-enlarge view. Built in JS from the same data-chart JSON the
 * small chart already carries, rather than a second server round trip —
 * nothing about a bigger canvas needs data the page does not already have.
 */
function openGrowthChartZoom(sourceCanvasId, title) {
  const source = document.getElementById(sourceCanvasId);
  const host = document.getElementById('modal-host');
  if (!source || !host) return;

  let data;
  try {
    data = JSON.parse(source.dataset.chart);
  } catch (err) {
    return;
  }

  // A double-click on "Zoom", or zooming straight from one chart to another,
  // calls this again before the modal has been closed. Overwriting the host's
  // markup below would detach the canvas the previous Chart instance is still
  // attached to without ever destroying it — an orphaned instance that keeps
  // its resize listener registered on window for the rest of the page's life.
  // Closing first is the same "destroy before you replace" rule
  // renderOneGrowthChart already applies to a single canvas, just at the
  // level of the whole zoom host.
  closeGrowthChartZoom();

  host.innerHTML =
    '<div class="modal-backdrop" onclick="if (event.target === this) closeGrowthChartZoom();">' +
    '  <div class="modal modal--wide" role="dialog" aria-modal="true" aria-labelledby="zoom-modal-title">' +
    '    <div class="modal__header">' +
    '      <h2 class="modal__title" id="zoom-modal-title">' + title + '</h2>' +
    '      <button type="button" class="modal__close" aria-label="Close" onclick="closeGrowthChartZoom();">&times;</button>' +
    '    </div>' +
    '    <div class="modal__body">' +
    '      <div class="chart-box__canvas chart-box__canvas--zoomed">' +
    '        <canvas id="growth-chart-zoomed"></canvas>' +
    '      </div>' +
    '    </div>' +
    '  </div>' +
    '</div>';

  renderOneGrowthChart(
    document.getElementById('growth-chart-zoomed'), data, growthChartColours(),
  );
}

function closeGrowthChartZoom() {
  const host = document.getElementById('modal-host');
  if (host) {
    // Chart.getChart(canvas) looks up one specific element by exact identity.
    // That missed instances often enough in practice — after several open/
    // close cycles the zoom stopped rendering at all, a blank canvas rather
    // than a chart, which is what an orphaned instance still holding the 2D
    // context looks like from the outside. Scanning Chart.js's own instance
    // registry for anything whose canvas sits anywhere inside this modal,
    // however it ended up there, is the version of "destroy before you
    // replace" that cannot miss.
    Object.keys(Chart.instances || {}).forEach(function (id) {
      const chart = Chart.instances[id];
      if (chart && chart.canvas && host.contains(chart.canvas)) chart.destroy();
    });
    host.innerHTML = '';
  }
}
