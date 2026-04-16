const JSON_URL = "/DAMspy-core/src/DAMspy_logs/latest_woym.json";
const REFRESH_MS = 2000;
const MISSING = "\u2014";
const RESULTS_ROUTE = "/results-analyser";
const CHANNEL_COLORS = [
  "#66d7ff",
  "#ffb266",
  "#86efac",
  "#f9a8d4",
  "#c4b5fd",
  "#fde047",
  "#fb7185",
  "#38bdf8"
];

const measurementElements = {
  page: document.getElementById("measurementPage"),
  banner: document.getElementById("statusBanner"),
  emptyState: document.getElementById("emptyState"),
  emptyMessage: document.getElementById("emptyMessage"),
  testGroup: document.getElementById("testGroup"),
  testMethod: document.getElementById("testMethod"),
  stateValue: document.getElementById("stateValue"),
  stateMessage: document.getElementById("stateMessage"),
  pointProgress: document.getElementById("pointProgress"),
  sweepProgress: document.getElementById("sweepProgress"),
  polarisation: document.getElementById("polarisation"),
  channel: document.getElementById("channel"),
  frequency: document.getElementById("frequency"),
  powerLevel: document.getElementById("powerLevel"),
  orientation: document.getElementById("orientation"),
  rxPeak: document.getElementById("rxPeak"),
  azimuth: document.getElementById("azimuth"),
  updatedAt: document.getElementById("updatedAt"),
  operatorPanel: document.getElementById("operatorPanel"),
  operatorMessage: document.getElementById("operatorMessage")
};

const analyserElements = {
  page: document.getElementById("resultsAnalyserPage"),
  banner: document.getElementById("analyserBanner"),
  subtitle: document.getElementById("analyserSubtitle"),
  yamlPickerButton: document.getElementById("yamlPickerButton"),
  yamlRefreshButton: document.getElementById("yamlRefreshButton"),
  yamlPickerPanel: document.getElementById("yamlPickerPanel"),
  yamlOptions: document.getElementById("yamlOptions"),
  selectedYamlPath: document.getElementById("selectedYamlPath"),
  selectedMeasurementName: document.getElementById("selectedMeasurementName"),
  globalPeakValue: document.getElementById("globalPeakValue"),
  testCountValue: document.getElementById("testCountValue"),
  measurementUpdatedAt: document.getElementById("measurementUpdatedAt"),
  testFolderList: document.getElementById("testFolderList"),
  plotGridContainer: document.getElementById("plotGridContainer")
};

const routeButtons = Array.from(document.querySelectorAll("[data-route]"));

const measurementState = {
  hasSuccessfulLoad: false,
  isRequestInFlight: false
};

const analyserState = {
  measurements: [],
  defaultMeasurementId: "",
  selectedMeasurementId: "",
  dataset: null,
  pickerOpen: false,
  listRequestInFlight: false,
  dataRequestSerial: 0
};

function getValue(source, path, fallback = MISSING) {
  let current = source;

  for (const key of path) {
    if (current == null || typeof current !== "object" || !(key in current)) {
      return fallback;
    }

    current = current[key];
  }

  if (current === null || current === undefined || current === "") {
    return fallback;
  }

  return current;
}

function formatNumber(value, digits) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) {
    return MISSING;
  }

  return numericValue.toFixed(digits);
}

function formatFrequency(value) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) {
    return MISSING;
  }

  if (Math.abs(numericValue) >= 1e9) {
    return (numericValue / 1e9).toFixed(3).replace(/\.?0+$/, "") + " GHz";
  }

  if (Math.abs(numericValue) >= 1e6) {
    return (numericValue / 1e6).toFixed(3).replace(/\.?0+$/, "") + " MHz";
  }

  if (Math.abs(numericValue) >= 1e3) {
    return (numericValue / 1e3).toFixed(3).replace(/\.?0+$/, "") + " kHz";
  }

  return numericValue + " Hz";
}

function formatDbm(value) {
  const formatted = formatNumber(value, 2);
  return formatted === MISSING ? MISSING : formatted + " dBm";
}

function formatDb(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(2) + " dB" : MISSING;
}

function formatSignedDb(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return MISSING;
  }
  return (numericValue >= 0 ? "+" : "") + numericValue.toFixed(2) + " dB";
}

function formatDegrees(value) {
  const formatted = formatNumber(value, 1);
  return formatted === MISSING ? MISSING : formatted + "\u00b0";
}

function formatProgress(indexValue, totalValue) {
  const indexText = indexValue === MISSING ? MISSING : String(indexValue);
  const totalText = totalValue === MISSING ? MISSING : String(totalValue);
  return indexText + " / " + totalText;
}

function formatLocalDateTime(value) {
  if (!value || value === MISSING) {
    return MISSING;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return date.toLocaleString();
}

function formatChannelLabel(channel) {
  return channel === MISSING || channel === null || channel === undefined ? "Ch ?" : "Ch " + channel;
}

function setBanner(element, kind, message) {
  element.className = "banner visible " + kind;
  element.textContent = message;
}

function clearBanner(element) {
  element.className = "banner";
  element.textContent = "";
}

function showEmptyState(message) {
  measurementElements.emptyState.classList.add("visible");
  measurementElements.emptyMessage.textContent = message;
}

function hideEmptyState() {
  measurementElements.emptyState.classList.remove("visible");
}

function renderMeasurementData(data) {
  const pointIndex = getValue(data, ["current_sweep", "point_index"]);
  const totalPoints = getValue(data, ["current_sweep", "total_points"]);
  const sweepIndex = getValue(data, ["current_sweep", "sweep_index"]);
  const totalSweeps = getValue(data, ["current_sweep", "total_sweeps"]);
  const operatorAction = getValue(data, ["operator_action"], "");

  measurementElements.testGroup.textContent = getValue(data, ["current_test_group"]);
  measurementElements.testMethod.textContent = getValue(data, ["current_test_method"]);
  measurementElements.stateValue.textContent = getValue(data, ["current_state", "state"]);
  measurementElements.stateMessage.textContent = getValue(data, ["current_state", "message"]);
  measurementElements.pointProgress.textContent = formatProgress(pointIndex, totalPoints);
  measurementElements.sweepProgress.textContent = formatProgress(sweepIndex, totalSweeps);
  measurementElements.polarisation.textContent = getValue(data, ["polarisation"]);
  measurementElements.channel.textContent = getValue(data, ["channel"]);
  measurementElements.frequency.textContent = formatFrequency(getValue(data, ["frequency_hz"]));
  measurementElements.powerLevel.textContent = getValue(data, ["power_level"]);
  measurementElements.orientation.textContent = getValue(data, ["orientation"]);
  measurementElements.rxPeak.textContent = formatDbm(getValue(data, ["last_measurement", "rx_peak_dbm"]));
  measurementElements.azimuth.textContent = formatDegrees(getValue(data, ["last_measurement", "azimuth_deg"]));
  measurementElements.updatedAt.textContent = formatLocalDateTime(getValue(data, ["updated_at"]));

  if (operatorAction) {
    measurementElements.operatorPanel.classList.add("visible");
    measurementElements.operatorMessage.textContent = operatorAction;
  } else {
    measurementElements.operatorPanel.classList.remove("visible");
    measurementElements.operatorMessage.textContent = "";
  }
}

async function refreshMeasurementData() {
  if (measurementState.isRequestInFlight) {
    return;
  }

  measurementState.isRequestInFlight = true;

  try {
    const response = await fetch(JSON_URL, { cache: "no-store" });

    if (!response.ok) {
      throw new Error("HTTP " + response.status + " " + response.statusText);
    }

    const data = await response.json();
    renderMeasurementData(data);
    measurementState.hasSuccessfulLoad = true;
    hideEmptyState();
    clearBanner(measurementElements.banner);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";

    if (!measurementState.hasSuccessfulLoad) {
      showEmptyState("Unable to load " + JSON_URL + ". " + message);
      setBanner(measurementElements.banner, "error", "DATA UNAVAILABLE: " + message);
    } else {
      setBanner(measurementElements.banner, "warning", "Showing last successful update. Refresh failed: " + message);
    }
  } finally {
    measurementState.isRequestInFlight = false;
  }
}

function normaliseRoute(pathname) {
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed || "/";
}

function getCurrentRoute() {
  return normaliseRoute(window.location.pathname) === RESULTS_ROUTE ? RESULTS_ROUTE : "/";
}

function setActiveRouteButtons(route) {
  for (const button of routeButtons) {
    const targetRoute = button.dataset.route === RESULTS_ROUTE ? RESULTS_ROUTE : "/";
    button.classList.toggle("is-active", targetRoute === route);
  }
}

function navigateTo(route) {
  const targetRoute = route === RESULTS_ROUTE ? RESULTS_ROUTE : "/";

  if (getCurrentRoute() !== targetRoute) {
    window.history.pushState({}, "", targetRoute);
  }

  renderRoute();
}

function renderRoute() {
  const route = getCurrentRoute();
  const showingAnalyser = route === RESULTS_ROUTE;

  setActiveRouteButtons(route);
  measurementElements.page.hidden = showingAnalyser;
  analyserElements.page.hidden = !showingAnalyser;
  document.title = showingAnalyser ? "DAMSpy Results Analyser" : "DAMSpy Visualiser";

  if (showingAnalyser) {
    ensureAnalyserReady();
  }
}

function bindRoutes() {
  for (const button of routeButtons) {
    button.addEventListener("click", () => navigateTo(button.dataset.route));
  }

  window.addEventListener("popstate", renderRoute);
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });

  if (!response.ok) {
    throw new Error("HTTP " + response.status + " " + response.statusText);
  }

  return response.json();
}

async function loadMeasurementList(options = {}) {
  if (analyserState.listRequestInFlight) {
    return;
  }

  analyserState.listRequestInFlight = true;

  try {
    const data = await fetchJson("/api/results-analyser/yamls");
    analyserState.measurements = Array.isArray(data.measurements) ? data.measurements : [];
    analyserState.defaultMeasurementId = data.default_measurement_id || "";

    const currentExists = analyserState.measurements.some((measurement) => measurement.measurement_id === analyserState.selectedMeasurementId);
    if (!currentExists) {
      analyserState.selectedMeasurementId = analyserState.defaultMeasurementId || "";
    }

    renderYamlPicker();

    if (options.autoLoad && analyserState.selectedMeasurementId) {
      await loadMeasurementDataset(analyserState.selectedMeasurementId);
    } else if (!analyserState.measurements.length) {
      renderAnalyserEmpty("No 1_meas_azimuth results were found under DAMspy-core.");
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    setBanner(analyserElements.banner, "error", "Unable to load the available YAML files: " + message);
    renderAnalyserEmpty("Unable to discover 1_meas_azimuth.yaml files.");
  } finally {
    analyserState.listRequestInFlight = false;
  }
}

async function loadMeasurementDataset(measurementId) {
  if (!measurementId) {
    renderAnalyserEmpty("Select a measurement to view its results.");
    return;
  }

  analyserState.selectedMeasurementId = measurementId;
  const requestId = ++analyserState.dataRequestSerial;
  analyserElements.subtitle.textContent = "Loading " + measurementId + "...";
  renderYamlPicker();

  try {
    const data = await fetchJson("/api/results-analyser/data?measurement_id=" + encodeURIComponent(measurementId));

    if (requestId !== analyserState.dataRequestSerial) {
      return;
    }

    analyserState.dataset = data;
    clearBanner(analyserElements.banner);
    renderAnalyserData(data);
  } catch (error) {
    if (requestId !== analyserState.dataRequestSerial) {
      return;
    }

    const message = error instanceof Error ? error.message : "Unknown error";
    setBanner(analyserElements.banner, "error", "Unable to load analyser data: " + message);
    renderAnalyserEmpty("The selected measurement could not be parsed.");
  }
}

async function ensureAnalyserReady() {
  if (!analyserState.measurements.length) {
    await loadMeasurementList({ autoLoad: true });
    return;
  }

  if (!analyserState.dataset && analyserState.selectedMeasurementId) {
    await loadMeasurementDataset(analyserState.selectedMeasurementId);
  }
}

function renderYamlPicker() {
  analyserElements.yamlPickerPanel.hidden = !analyserState.pickerOpen;
  analyserElements.yamlOptions.replaceChildren();

  if (!analyserState.measurements.length) {
    const empty = document.createElement("div");
    empty.className = "plot-empty";
    empty.textContent = "No YAML files discovered yet.";
    analyserElements.yamlOptions.append(empty);
    return;
  }

  for (const measurement of analyserState.measurements) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "yaml-option";
    button.classList.toggle("is-selected", measurement.measurement_id === analyserState.selectedMeasurementId);

    const title = document.createElement("span");
    title.className = "yaml-option-title";
    title.textContent = measurement.measurement_name;

    const meta = document.createElement("span");
    meta.className = "yaml-option-meta";
    meta.textContent = measurement.yaml_relative_path + " | Updated " + formatLocalDateTime(measurement.updated_at);

    button.append(title, meta);
    button.addEventListener("click", async () => {
      analyserState.pickerOpen = false;
      renderYamlPicker();
      await loadMeasurementDataset(measurement.measurement_id);
    });

    analyserElements.yamlOptions.append(button);
  }
}

function renderAnalyserEmpty(message) {
  analyserState.dataset = null;
  analyserElements.subtitle.textContent = message;
  analyserElements.selectedYamlPath.textContent = MISSING;
  analyserElements.selectedMeasurementName.textContent = MISSING;
  analyserElements.globalPeakValue.textContent = MISSING;
  analyserElements.testCountValue.textContent = MISSING;
  analyserElements.measurementUpdatedAt.textContent = MISSING;
  analyserElements.testFolderList.replaceChildren();
  analyserElements.plotGridContainer.replaceChildren();

  const empty = document.createElement("div");
  empty.className = "plot-grid-empty";
  empty.textContent = message;
  analyserElements.plotGridContainer.append(empty);
}

function renderAnalyserData(data) {
  analyserElements.subtitle.textContent = data.measurement_name + " | " + data.columns.length + " orientation column(s) | " + data.rows.length + " polarisation row(s)";
  analyserElements.selectedYamlPath.textContent = data.yaml_relative_path || MISSING;
  analyserElements.selectedMeasurementName.textContent = data.measurement_name || MISSING;
  analyserElements.globalPeakValue.textContent = formatDbm(data.global_peak_dbm);
  analyserElements.testCountValue.textContent = String(data.folders.length);
  analyserElements.measurementUpdatedAt.textContent = "Updated " + formatLocalDateTime(data.updated_at);

  renderFolderList(data.folders);
  renderPlotGrid(data);
}

function renderFolderList(folders) {
  analyserElements.testFolderList.replaceChildren();

  if (!folders.length) {
    const empty = document.createElement("div");
    empty.className = "plot-empty";
    empty.textContent = "No subdirectories with metadata and CSV results were found.";
    analyserElements.testFolderList.append(empty);
    return;
  }

  for (const folder of folders) {
    const item = document.createElement("div");
    item.className = "folder-chip";

    const title = document.createElement("span");
    title.className = "folder-title";
    title.textContent = folder.folder_name;

    const meta = document.createElement("span");
    meta.className = "folder-meta";
    meta.textContent = [folder.orientation, folder.polarisation, formatChannelLabel(folder.channel), formatFrequency(folder.frequency_hz)]
      .filter((value) => value && value !== MISSING)
      .join(" | ");

    item.append(title, meta);
    analyserElements.testFolderList.append(item);
  }
}

function naturalSortValue(value) {
  return String(value).replace(/(\d+)/g, (_, digits) => digits.padStart(12, "0"));
}

function sortSeries(series) {
  return [...series].sort((left, right) => {
    const leftNumber = Number(left.channel);
    const rightNumber = Number(right.channel);

    if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
      return leftNumber - rightNumber;
    }

    return naturalSortValue(left.channel).localeCompare(naturalSortValue(right.channel));
  });
}

function plotKey(polarisation, orientation) {
  return polarisation + "::" + orientation;
}

function renderPlotGrid(data) {
  analyserElements.plotGridContainer.replaceChildren();

  if (!data.rows.length || !data.columns.length) {
    const empty = document.createElement("div");
    empty.className = "plot-grid-empty";
    empty.textContent = "The selected measurement did not contain enough data to build the grid.";
    analyserElements.plotGridContainer.append(empty);
    return;
  }

  const plotMap = new Map(data.plots.map((plot) => [plotKey(plot.polarisation, plot.orientation), plot]));
  const grid = document.createElement("div");
  grid.className = "plot-grid";
  grid.style.gridTemplateColumns = `8.5rem repeat(${data.columns.length}, minmax(18rem, 1fr))`;

  const corner = document.createElement("div");
  corner.className = "plot-grid-corner";
  corner.textContent = "Polarisation \\ Orientation";
  grid.append(corner);

  for (const column of data.columns) {
    const header = document.createElement("div");
    header.className = "plot-grid-col-header";
    header.textContent = column;
    grid.append(header);
  }

  for (const row of data.rows) {
    const rowHeader = document.createElement("div");
    rowHeader.className = "plot-grid-row-header";
    rowHeader.textContent = row;
    grid.append(rowHeader);

    for (const column of data.columns) {
      const plot = plotMap.get(plotKey(row, column));
      grid.append(createPlotCard(plot, row, column, data));
    }
  }

  analyserElements.plotGridContainer.append(grid);
}

function createPlotCard(plot, row, column, dataset) {
  if (!plot || !plot.series.length) {
    const empty = document.createElement("div");
    empty.className = "plot-card";

    const label = document.createElement("div");
    label.className = "plot-empty";
    label.textContent = "No data for " + row + " / " + column + ".";
    empty.append(label);
    return empty;
  }

  const card = document.createElement("article");
  card.className = "plot-card";

  const header = document.createElement("div");
  header.className = "plot-card-header";

  const titleGroup = document.createElement("div");
  const title = document.createElement("h3");
  title.className = "plot-card-title";
  title.textContent = row + " | " + column;

  const subtitle = document.createElement("p");
  subtitle.className = "plot-card-subtitle";
  subtitle.textContent = plot.series.length + " overlaid channel(s)";

  titleGroup.append(title, subtitle);
  header.append(titleGroup);

  const readout = document.createElement("div");
  readout.className = "plot-readout";

  const series = sortSeries(plot.series).map((entry, index) => ({
    ...entry,
    color: CHANNEL_COLORS[index % CHANNEL_COLORS.length]
  }));

  updatePlotReadout(readout, null, [], false);

  const svg = createPlotSvg(series, dataset, readout, row, column);
  const legend = createPlotLegend(series, dataset.global_peak_dbm);

  card.append(header, readout, svg, legend);
  return card;
}

function createPlotLegend(series, globalPeakDbm) {
  const legend = document.createElement("div");
  legend.className = "plot-legend";

  const reference = document.createElement("div");
  reference.className = "legend-reference";
  reference.textContent = "Ref peak " + formatDbm(globalPeakDbm);
  legend.append(reference);

  for (const entry of series) {
    const item = document.createElement("div");
    item.className = "legend-item";

    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = entry.color;

    const text = document.createElement("span");
    text.textContent = formatChannelLabel(entry.channel) + " peak " + formatDbm(entry.peak_dbm) + " (" + formatSignedDb(entry.peak_offset_db) + ")";

    item.append(swatch, text);
    legend.append(item);
  }

  return legend;
}

function createSvgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);

  for (const [key, value] of Object.entries(attributes)) {
    if (value !== null && value !== undefined) {
      element.setAttribute(key, String(value));
    }
  }

  return element;
}

function buildTicks(minValue, maxValue, count) {
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
    return [];
  }

  if (minValue === maxValue) {
    return [minValue];
  }

  const ticks = [];
  const step = (maxValue - minValue) / Math.max(count - 1, 1);

  for (let index = 0; index < count; index += 1) {
    ticks.push(minValue + step * index);
  }

  return ticks;
}

function findNearestNumber(sortedValues, target) {
  if (!sortedValues.length) {
    return null;
  }

  let nearest = sortedValues[0];
  let smallestDistance = Math.abs(sortedValues[0] - target);

  for (const value of sortedValues) {
    const distance = Math.abs(value - target);
    if (distance < smallestDistance) {
      nearest = value;
      smallestDistance = distance;
    }
  }

  return nearest;
}

function updatePlotReadout(container, angle, rows, pinned) {
  container.replaceChildren();

  if (angle === null || !rows.length) {
    const title = document.createElement("div");
    title.className = "plot-readout-title";
    title.textContent = "Cursor readout";

    const hint = document.createElement("div");
    hint.className = "plot-readout-hint";
    hint.textContent = "Hover to inspect values. Click to pin the current angle.";

    container.append(title, hint);
    return;
  }

  const title = document.createElement("div");
  title.className = "plot-readout-title";
  title.textContent = "Angle " + formatDegrees(angle) + (pinned ? " | pinned" : "");
  container.append(title);

  for (const row of rows) {
    const line = document.createElement("div");
    line.className = "plot-readout-row";

    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = row.color;

    const text = document.createElement("span");
    text.textContent = formatChannelLabel(row.channel) + ": " + formatDb(row.normalised_db) + " | " + formatDbm(row.rx_peak_dbm);

    line.append(swatch, text);
    container.append(line);
  }
}

function createPlotSvg(series, dataset, readout, row, column) {
  const width = 360;
  const height = 250;
  const margin = { top: 18, right: 14, bottom: 38, left: 54 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const xMin = Number(dataset.x_range.min);
  const xMax = Number(dataset.x_range.max);
  const yMin = Number(dataset.y_range.min);
  const yMax = Number(dataset.y_range.max);

  const svg = createSvgElement("svg", {
    class: "plot-svg",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Plot for polarisation ${row} and orientation ${column}`
  });

  const xScale = (value) => margin.left + ((value - xMin) / (xMax - xMin || 1)) * chartWidth;
  const yScale = (value) => margin.top + ((yMax - value) / (yMax - yMin || 1)) * chartHeight;
  const sampleAngles = Array.from(new Set(series.flatMap((entry) => entry.points.map((point) => Number(point.angle_deg)))))
    .filter((value) => Number.isFinite(value))
    .sort((left, right) => left - right);

  const xTicks = buildTicks(xMin, xMax, 7);
  const yTicks = buildTicks(yMin, yMax, 6);

  for (const tick of yTicks) {
    const y = yScale(tick);
    svg.append(createSvgElement("line", {
      class: "grid-line",
      x1: margin.left,
      y1: y,
      x2: margin.left + chartWidth,
      y2: y
    }));

    const label = createSvgElement("text", {
      class: "tick-label",
      x: margin.left - 8,
      y: y + 4,
      "text-anchor": "end"
    });
    label.textContent = tick.toFixed(0);
    svg.append(label);
  }

  for (const tick of xTicks) {
    const x = xScale(tick);
    svg.append(createSvgElement("line", {
      class: "grid-line",
      x1: x,
      y1: margin.top,
      x2: x,
      y2: margin.top + chartHeight
    }));

    const label = createSvgElement("text", {
      class: "tick-label",
      x,
      y: margin.top + chartHeight + 18,
      "text-anchor": "middle"
    });
    label.textContent = tick.toFixed(0);
    svg.append(label);
  }

  svg.append(createSvgElement("line", {
    class: "axis-line",
    x1: margin.left,
    y1: margin.top + chartHeight,
    x2: margin.left + chartWidth,
    y2: margin.top + chartHeight
  }));

  svg.append(createSvgElement("line", {
    class: "axis-line",
    x1: margin.left,
    y1: margin.top,
    x2: margin.left,
    y2: margin.top + chartHeight
  }));

  const xAxisLabel = createSvgElement("text", {
    class: "axis-label",
    x: margin.left + chartWidth / 2,
    y: height - 8,
    "text-anchor": "middle"
  });
  xAxisLabel.textContent = "Azimuth (deg)";
  svg.append(xAxisLabel);

  const yAxisLabel = createSvgElement("text", {
    class: "axis-label",
    x: 14,
    y: margin.top + chartHeight / 2,
    transform: `rotate(-90 14 ${margin.top + chartHeight / 2})`,
    "text-anchor": "middle"
  });
  yAxisLabel.textContent = "Relative To Global Peak (dB)";
  svg.append(yAxisLabel);

  for (const entry of series) {
    const points = [...entry.points]
      .map((point) => ({
        angle_deg: Number(point.angle_deg),
        rx_peak_dbm: Number(point.rx_peak_dbm),
        normalised_db: Number(point.normalised_db)
      }))
      .sort((left, right) => left.angle_deg - right.angle_deg);

    const commands = points.map((point, index) => {
      const x = xScale(point.angle_deg);
      const y = yScale(point.normalised_db);
      return (index === 0 ? "M" : "L") + x.toFixed(2) + " " + y.toFixed(2);
    });

    svg.append(createSvgElement("path", {
      class: "series-line",
      d: commands.join(" "),
      stroke: entry.color
    }));
  }

  const crosshair = createSvgElement("line", {
    class: "crosshair",
    x1: margin.left,
    y1: margin.top,
    x2: margin.left,
    y2: margin.top + chartHeight,
    visibility: "hidden"
  });
  svg.append(crosshair);

  const markerLayer = createSvgElement("g");
  svg.append(markerLayer);

  const overlay = createSvgElement("rect", {
    x: margin.left,
    y: margin.top,
    width: chartWidth,
    height: chartHeight,
    fill: "transparent"
  });
  overlay.style.cursor = "crosshair";
  svg.append(overlay);

  let pinned = false;

  function clearHover() {
    crosshair.setAttribute("visibility", "hidden");
    markerLayer.replaceChildren();
    updatePlotReadout(readout, null, [], false);
  }

  function renderHover(angle, pinState) {
    const nearestAngle = findNearestNumber(sampleAngles, angle);

    if (nearestAngle === null) {
      clearHover();
      return;
    }

    const rows = [];
    markerLayer.replaceChildren();
    crosshair.setAttribute("visibility", "visible");
    crosshair.setAttribute("x1", xScale(nearestAngle));
    crosshair.setAttribute("x2", xScale(nearestAngle));

    for (const entry of series) {
      const point = entry.points.find((candidate) => Number(candidate.angle_deg) === nearestAngle);

      if (!point) {
        continue;
      }

      const marker = createSvgElement("circle", {
        class: "hover-marker",
        cx: xScale(nearestAngle),
        cy: yScale(Number(point.normalised_db)),
        r: 4,
        fill: entry.color
      });

      markerLayer.append(marker);
      rows.push({
        channel: entry.channel,
        color: entry.color,
        normalised_db: Number(point.normalised_db),
        rx_peak_dbm: Number(point.rx_peak_dbm)
      });
    }

    updatePlotReadout(readout, nearestAngle, rows, pinState);
  }

  function eventToAngle(event) {
    const bounds = overlay.getBoundingClientRect();
    const x = Math.max(0, Math.min(bounds.width, event.clientX - bounds.left));
    const ratio = bounds.width === 0 ? 0 : x / bounds.width;
    return xMin + ratio * (xMax - xMin);
  }

  overlay.addEventListener("pointermove", (event) => {
    if (!pinned) {
      renderHover(eventToAngle(event), false);
    }
  });

  overlay.addEventListener("pointerleave", () => {
    if (!pinned) {
      clearHover();
    }
  });

  overlay.addEventListener("click", (event) => {
    if (pinned) {
      pinned = false;
      clearHover();
      return;
    }

    pinned = true;
    renderHover(eventToAngle(event), true);
  });

  return svg;
}

function bindAnalyserControls() {
  analyserElements.yamlPickerButton.addEventListener("click", async () => {
    analyserState.pickerOpen = !analyserState.pickerOpen;
    renderYamlPicker();

    if (analyserState.pickerOpen) {
      await loadMeasurementList();
    }
  });

  analyserElements.yamlRefreshButton.addEventListener("click", async () => {
    await loadMeasurementList();
    renderYamlPicker();
  });
}

bindRoutes();
bindAnalyserControls();
renderRoute();
refreshMeasurementData();
window.setInterval(refreshMeasurementData, REFRESH_MS);
