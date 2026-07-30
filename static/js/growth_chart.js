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

function renderGrowthCharts() {
  const container = document.getElementById('growth-charts');
  if (!container || typeof Chart === 'undefined') return;

  const teal = growthThemeColour('--brand-teal', '#17a398');
  const dark = growthThemeColour('--brand-dark', '#414e54');
  const muted = growthThemeColour('--text-faint', '#93a3a8');
  const border = growthThemeColour('--border', '#d8e2e2');

  container.querySelectorAll('canvas[data-chart]').forEach(function (canvas) {
    // Re-rendering the tab must not stack a second chart on the same canvas.
    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    let data;
    try {
      data = JSON.parse(canvas.dataset.chart);
    } catch (err) {
      return;
    }

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

    datasets.push({
      label: 'This patient',
      data: data.points.map(function (p) {
        return { x: p.month, y: p.value, percentile: p.percentile, date: p.date };
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

    new Chart(canvas, {
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
              // Only the patient's line needs a legend entry; seven percentile
              // labels would swamp a small chart.
              filter: function (item) { return item.text === 'This patient'; }
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
                let text = item.dataset.label + ': ' + item.parsed.y + ' ' + data.unit;
                if (raw && raw.percentile !== null && raw.percentile !== undefined) {
                  text += '  (' + raw.percentile + 'th centile)';
                }
                return text;
              }
            }
          }
        }
      }
    });
  });
}
