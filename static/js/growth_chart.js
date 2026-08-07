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
  // cut-offs (drawn in warning colours, since they mark overweight and
  // obesity), and the height chart's mid-parental target (drawn in a
  // neutral informational colour — it is a target, not a risk).
  (data.cutoffs || []).forEach(function (line) {
    let color = warning;
    if (line.key === 'Eq27') color = danger;
    else if (line.key === 'MPH') color = info;
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

  return {
    type: 'line',
    data: { datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Age (years)', color: muted, font: { size: 11 } },
          ticks: {
            color: muted,
            font: { size: 10 },
            // Tables are keyed in months; clinicians read charts in years.
            callback: function (value) { return (value / 12).toFixed(0); }
          },
          grid: { color: border }
        },
        y: {
          title: { display: true, text: data.unit, color: muted, font: { size: 11 } },
          ticks: { color: muted, font: { size: 10 } },
          grid: { color: border }
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
  const canvas = document.getElementById('growth-chart-zoomed');
  if (canvas) {
    const chart = Chart.getChart(canvas);
    if (chart) chart.destroy();
  }
  const host = document.getElementById('modal-host');
  if (host) host.innerHTML = '';
}
