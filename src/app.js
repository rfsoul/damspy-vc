const JSON_URL = "/DAMspy-core/src/DAMspy_logs/latest_woym.json";
const REFRESH_MS = 2000;
const MISSING = "\u2014";
const RESULTS_ROUTE = "/results-analyser";
const THEME_STORAGE_KEY = "damspy-theme";
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
const PLOT_DISPLAY_MODES = {
  E_OVER_EMAX: "e_over_emax",
  DB: "db"
};
const E_OVER_EMAX_DB_GUIDES = [-20, -10, -6, -3];
const FIXED_CHANNEL_COLORS = new Map([
  ["0", "#ef4444"],
  ["40", "#22c55e"],
  ["80", "#3b82f6"]
]);

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
  measurementDetailsPanel: document.getElementById("measurementDetailsPanel"),
  testFolderPanel: document.getElementById("testFolderPanel"),
  yamlPickerButton: document.getElementById("yamlPickerButton"),
  yamlRefreshButton: document.getElementById("yamlRefreshButton"),
  yamlPickerPanel: document.getElementById("yamlPickerPanel"),
  yamlOptions: document.getElementById("yamlOptions"),
  selectedYamlPath: document.getElementById("selectedYamlPath"),
  selectedMeasurementName: document.getElementById("selectedMeasurementName"),
  globalPeakValue: document.getElementById("globalPeakValue"),
  testCountValue: document.getElementById("testCountValue"),
  testFolderSummaryText: document.getElementById("testFolderSummaryText"),
  measurementUpdatedAt: document.getElementById("measurementUpdatedAt"),
  testFolderList: document.getElementById("testFolderList"),
  plotGridContainer: document.getElementById("plotGridContainer"),
  plotModeDescription: document.getElementById("plotModeDescription"),
  plotModeButtons: Array.from(document.querySelectorAll("[data-plot-mode]"))
};
const themeElements = {
  toggleButton: document.getElementById("themeToggleButton")
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
  plotDisplayMode: PLOT_DISPLAY_MODES.E_OVER_EMAX,
  pickerOpen: false,
  listRequestInFlight: false,
  dataRequestSerial: 0
};
const uiState = {
  theme: "dark"
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
  const formatted = formatNumber(value, 1);
  return formatted === MISSING ? MISSING : formatted + " dBm";
}

function formatDb(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(1) + " dB" : MISSING;
}

function formatDbd(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(1) + " dBd" : MISSING;
}

function formatSignedDb(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return MISSING;
  }
  return (numericValue >= 0 ? "+" : "") + numericValue.toFixed(1) + " dB";
}

function formatEOverEmax(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(3) + " E/Emax" : MISSING;
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

function formatPolarisationLabel(value) {
  const text = value === null || value === undefined ? "" : String(value).trim();

  if (!text) {
    return MISSING;
  }

  const upper = text.toUpperCase();
  if (upper === "H") {
    return "Hpol";
  }
  if (upper === "V") {
    return "Vpol";
  }

  return text;
}

function getChannelColor(channel, index) {
  const key = channel === null || channel === undefined ? "" : String(channel).trim();
  return FIXED_CHANNEL_COLORS.get(key) || CHANNEL_COLORS[index % CHANNEL_COLORS.length];
}

function formatYamlSummaryValue(value) {
  if (value === null || value === undefined || value === "") {
    return MISSING;
  }

  return String(value);
}

function buildAnalyserSubtitle(data) {
  return [
    'DUT_product: "' + formatYamlSummaryValue(data.dut_product) + '"',
    'DUT_serial_number: "' + formatYamlSummaryValue(data.dut_serial_number) + '"',
    'foldername_comment: "' + formatYamlSummaryValue(data.foldername_comment) + '"'
  ].join("\n");
}

function buildTestFolderSummary(data) {
  const folders = Array.isArray(data.folders) ? data.folders : [];
  const channelCount = new Set(
    folders
      .map((folder) => folder && folder.channel)
      .filter((channel) => channel !== null && channel !== undefined && channel !== "")
      .map((channel) => String(channel).trim())
  ).size;

  return "Folders/tests = " + String(folders.length)
    + "  " + String(Array.isArray(data.rows) ? data.rows.length : 0) + " Pol"
    + "  " + String(Array.isArray(data.columns) ? data.columns.length : 0) + " Ori"
    + "  " + String(channelCount) + " ch";
}

function dbToAmplitudeRatio(value) {
  const numericValue = Number(value);

  if (!Number.isFinite(numericValue)) {
    return Number.NaN;
  }

  return Math.pow(10, numericValue / 20);
}

function readCssVariable(name, fallback) {
  const resolved = window.getComputedStyle(document.body).getPropertyValue(name).trim();
  return resolved || fallback;
}

function readStoredTheme() {
  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    return storedTheme === "light" || storedTheme === "dark" ? storedTheme : "dark";
  } catch (error) {
    return "dark";
  }
}

function applyTheme(theme) {
  uiState.theme = theme === "light" ? "light" : "dark";
  document.body.dataset.theme = uiState.theme;

  if (themeElements.toggleButton) {
    const nextLabel = uiState.theme === "dark" ? "Light Mode" : "Dark Mode";
    themeElements.toggleButton.textContent = nextLabel;
    themeElements.toggleButton.setAttribute("aria-label", "Switch to " + nextLabel.toLowerCase());
    themeElements.toggleButton.setAttribute("aria-pressed", String(uiState.theme === "light"));
  }

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, uiState.theme);
  } catch (error) {
    // Ignore storage failures and keep the in-memory theme.
  }

  if (analyserState.dataset) {
    renderPlotGrid(analyserState.dataset);
  }
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

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...options
  });

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

    const meta = document.createElement("span");
    meta.className = "yaml-option-meta";
    meta.textContent = measurement.yaml_relative_path + " | Updated " + formatLocalDateTime(measurement.updated_at);

    button.append(meta);
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
  analyserElements.testFolderSummaryText.textContent = "Folders/tests = -";
  analyserElements.measurementUpdatedAt.textContent = MISSING;
  analyserElements.testFolderList.replaceChildren();
  analyserElements.plotGridContainer.replaceChildren();
  updatePlotModeUi();

  const empty = document.createElement("div");
  empty.className = "plot-grid-empty";
  empty.textContent = message;
  analyserElements.plotGridContainer.append(empty);
}

function getPlotModeDescription() {
  return analyserState.plotDisplayMode === PLOT_DISPLAY_MODES.E_OVER_EMAX
    ? "Hover to inspect. Click to pin. E/Emax with -3 dB to -20 dB guide circles."
    : "Hover to inspect. Click to pin. dB values are relative to the selected set's strongest peak.";
}

function updatePlotModeUi() {
  analyserElements.plotModeDescription.textContent = getPlotModeDescription();

  for (const button of analyserElements.plotModeButtons) {
    button.classList.toggle("is-active", button.dataset.plotMode === analyserState.plotDisplayMode);
  }
}

function renderAnalyserData(data) {
  analyserElements.subtitle.textContent = buildAnalyserSubtitle(data);
  analyserElements.selectedYamlPath.textContent = data.yaml_relative_path || MISSING;
  analyserElements.selectedMeasurementName.textContent = data.measurement_name || MISSING;
  analyserElements.globalPeakValue.textContent = formatDbm(data.global_peak_dbm);
  analyserElements.testCountValue.textContent = String(data.folders.length);
  analyserElements.testFolderSummaryText.textContent = buildTestFolderSummary(data);
  analyserElements.measurementUpdatedAt.textContent = "Updated " + formatLocalDateTime(data.updated_at);

  updatePlotModeUi();
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

function getPlotPeakDbm(series) {
  const peaks = series
    .flatMap((entry) => entry.points.map((point) => Number(point.rx_peak_dbm)))
    .filter((value) => Number.isFinite(value));

  return peaks.length ? Math.max(...peaks) : Number.NaN;
}

function buildDbRingTicks(minValue) {
  const lowerBound = Number(minValue);

  if (!Number.isFinite(lowerBound)) {
    return [];
  }

  const preferredTicks = [0, -3, -6, -10, -20, -30, -40, -50, -60, -80, -100];
  const ticks = preferredTicks.filter((value) => value <= 0 && value >= lowerBound).sort((left, right) => left - right);

  if (!ticks.length || ticks[0] !== lowerBound) {
    ticks.unshift(lowerBound);
  }

  if (ticks[ticks.length - 1] !== 0) {
    ticks.push(0);
  }

  return ticks.map((tick) => ({
    value: tick,
    label: tick === 0 ? "" : tick.toFixed(0) + " dB",
    className: tick === 0 ? "polar-outer-ring" : tick === -3 ? "polar-reference" : "polar-ring"
  }));
}

function buildEOverEmaxRingTicks() {
  return [
    ...E_OVER_EMAX_DB_GUIDES.map((guideDb) => ({
      value: dbToAmplitudeRatio(guideDb),
      label: guideDb.toFixed(0) + " dB",
      className: guideDb === -3 ? "polar-reference" : "polar-ring"
    })),
    {
      value: 1,
      label: "",
      className: "polar-outer-ring"
    }
  ];
}

function prepareSeriesForPlot(series, dataset, mode) {
  const plotPeakDbm = getPlotPeakDbm(series);
  const globalPeakDbm = Number(dataset.global_peak_dbm);

  return {
    plotPeakDbm,
    globalPeakDbm,
    yMin: mode === PLOT_DISPLAY_MODES.E_OVER_EMAX ? 0 : Number(dataset.y_range.min),
    yMax: mode === PLOT_DISPLAY_MODES.E_OVER_EMAX ? 1 : 0,
    yLabel: mode === PLOT_DISPLAY_MODES.E_OVER_EMAX ? "E/Emax (per plot, dB guides)" : "Relative To Global Peak (dB)",
    ringTicks: mode === PLOT_DISPLAY_MODES.E_OVER_EMAX ? buildEOverEmaxRingTicks() : buildDbRingTicks(Number(dataset.y_range.min)),
    series: series.map((entry) => ({
      ...entry,
      peak_e_over_emax: dbToAmplitudeRatio(Number(entry.peak_dbm) - plotPeakDbm),
      points: entry.points
        .map((point) => {
          const rxPeakDbm = Number(point.rx_peak_dbm);
          const normalisedDb = Number(point.normalised_db);
          const relativeToPlotDb = rxPeakDbm - plotPeakDbm;
          return {
            angle_deg: Number(point.angle_deg),
            rx_peak_dbm: rxPeakDbm,
            normalised_db: normalisedDb,
            e_over_emax: dbToAmplitudeRatio(relativeToPlotDb),
            display_value: mode === PLOT_DISPLAY_MODES.E_OVER_EMAX ? dbToAmplitudeRatio(relativeToPlotDb) : normalisedDb
          };
        })
        .sort((left, right) => left.angle_deg - right.angle_deg)
    }))
  };
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
  grid.style.gridTemplateColumns = `4.8rem repeat(${data.rows.length}, minmax(0, 1fr))`;

  const corner = document.createElement("div");
  corner.className = "plot-grid-corner";
  corner.textContent = "Orientation \\ Polarisation";
  grid.append(corner);

  for (const column of data.rows) {
    const header = document.createElement("div");
    header.className = "plot-grid-col-header";
    header.textContent = formatPolarisationLabel(column);
    grid.append(header);
  }

  for (const row of data.columns) {
    const rowHeader = document.createElement("div");
    rowHeader.className = "plot-grid-row-header";

    const rowLabel = document.createElement("div");
    rowLabel.className = "plot-grid-row-label";
    rowLabel.textContent = row;
    rowHeader.append(rowLabel);

    grid.append(rowHeader);

    for (const column of data.rows) {
      const plot = plotMap.get(plotKey(column, row));
      const orientationImageUrl = column === data.rows[0] && data.orientation_images ? data.orientation_images[row] : null;
      grid.append(createPlotCard(plot, column, row, data, analyserState.plotDisplayMode, orientationImageUrl));
    }
  }

  analyserElements.plotGridContainer.append(grid);
}

function createPlotCard(plot, row, column, dataset, mode, orientationImageUrl = null) {
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

  const readout = document.createElement("div");
  readout.className = "plot-readout";

  const colorisedSeries = sortSeries(plot.series).map((entry, index) => ({
    ...entry,
    color: getChannelColor(entry.channel, index)
  }));
  const preparedPlot = prepareSeriesForPlot(colorisedSeries, dataset, mode);

  updatePlotReadout(readout, null, [], false, mode);

  const svg = createPlotSvg(preparedPlot, readout, row, column, mode);
  const legend = createPlotLegend(preparedPlot, mode);
  const visual = document.createElement("div");
  visual.className = "plot-visual";

  if (orientationImageUrl) {
    const orientationImage = document.createElement("img");
    orientationImage.className = "plot-orientation-photo";
    orientationImage.src = orientationImageUrl;
    orientationImage.alt = column + " orientation";
    orientationImage.loading = "lazy";
    orientationImage.decoding = "async";
    orientationImage.addEventListener("error", () => {
      orientationImage.remove();
    });
    visual.append(orientationImage);
  }

  visual.append(svg, legend);

  card.append(readout, visual);
  return card;
}

function createPlotLegend(preparedPlot, mode) {
  const legend = document.createElement("div");
  legend.className = "plot-legend";

  const reference = document.createElement("div");
  reference.className = "legend-reference";
  reference.textContent = mode === PLOT_DISPLAY_MODES.E_OVER_EMAX
    ? "Max " + formatDbm(preparedPlot.plotPeakDbm)
    : "Ref " + formatDbm(preparedPlot.globalPeakDbm);
  legend.append(reference);

  for (const entry of preparedPlot.series) {
    const item = document.createElement("div");
    item.className = "legend-item";

    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = entry.color;

    const primary = document.createElement("div");
    primary.className = "legend-primary";
    const parts = [
      mode === PLOT_DISPLAY_MODES.E_OVER_EMAX
        ? formatChannelLabel(entry.channel) + " " + formatEOverEmax(entry.peak_e_over_emax)
        : formatChannelLabel(entry.channel) + " " + formatDbm(entry.peak_dbm),
      mode === PLOT_DISPLAY_MODES.E_OVER_EMAX
        ? formatDbm(entry.peak_dbm)
        : formatSignedDb(entry.peak_offset_db)
    ];
    if (entry.eirp_dbm !== null && entry.eirp_dbm !== undefined) {
      parts.push("EIRP " + formatDbm(entry.eirp_dbm));
    }
    if (entry.gain_dbd !== null && entry.gain_dbd !== undefined) {
      parts.push(formatDbd(entry.gain_dbd));
    }
    primary.textContent = parts.join(" | ");

    item.append(swatch, primary);
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

function updatePlotReadout(container, angle, rows, pinned, mode) {
  container.replaceChildren();

  if (angle === null || !rows.length) {
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
    text.textContent = mode === PLOT_DISPLAY_MODES.E_OVER_EMAX
      ? formatChannelLabel(row.channel) + ": " + formatEOverEmax(row.e_over_emax) + " | " + formatDbm(row.rx_peak_dbm)
      : formatChannelLabel(row.channel) + ": " + formatDb(row.normalised_db) + " | " + formatDbm(row.rx_peak_dbm);

    line.append(swatch, text);
    container.append(line);
  }
}

function createPlotSvg(preparedPlot, readout, row, column, mode) {
  const width = 380;
  const height = 340;
  const centerX = width / 2;
  const centerY = 156;
  const radius = 136;
  const yMin = Number(preparedPlot.yMin);
  const yMax = Number(preparedPlot.yMax);
  const angleLabels = [0, 45, 90, 135, 180, 225, 270, 315];
  const sampleAngles = Array.from(new Set(preparedPlot.series.flatMap((entry) => entry.points.map((point) => Number(point.angle_deg)))))
    .filter((value) => Number.isFinite(value))
    .sort((left, right) => left - right);

  const svg = createSvgElement("svg", {
    class: "plot-svg",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Plot for polarisation ${row} and orientation ${column}`
  });
  const svgPalette = {
    paperFill: readCssVariable("--plot-paper-fill", "#ffffff"),
    paperStroke: readCssVariable("--plot-paper-stroke", "rgba(17, 32, 51, 0.12)"),
    gridStroke: readCssVariable("--plot-grid-stroke", "rgba(60, 72, 88, 0.34)"),
    axisStroke: readCssVariable("--plot-axis-stroke", "rgba(60, 72, 88, 0.56)"),
    referenceStroke: readCssVariable("--plot-reference-stroke", "rgba(60, 72, 88, 0.74)"),
    labelFill: readCssVariable("--plot-label", "#425466"),
    crosshairStroke: readCssVariable("--crosshair-stroke", "rgba(60, 72, 88, 0.68)"),
    hoverMarkerStroke: readCssVariable("--hover-marker-stroke", "rgba(4, 7, 12, 0.95)")
  };

  svg.append(createSvgElement("rect", {
    class: "polar-paper",
    x: 6,
    y: 6,
    width: width - 12,
    height: height - 12,
    rx: 18,
    ry: 18,
    fill: svgPalette.paperFill,
    stroke: svgPalette.paperStroke,
    "stroke-width": 1
  }));

  const radialScale = (value) => {
    const numericValue = Number(value);

    if (!Number.isFinite(numericValue)) {
      return 0;
    }

    if (mode === PLOT_DISPLAY_MODES.E_OVER_EMAX) {
      return radius * Math.max(0, Math.min(1, numericValue));
    }

    return radius * ((numericValue - yMin) / (yMax - yMin || 1));
  };

  const polarToCartesian = (angleDeg, value) => {
    const angleRadians = (Number(angleDeg) * Math.PI) / 180;
    const radialDistance = radialScale(value);
    return {
      x: centerX + radialDistance * Math.sin(angleRadians),
      y: centerY - radialDistance * Math.cos(angleRadians)
    };
  };

  for (const angle of angleLabels) {
    const spokeEnd = polarToCartesian(angle, yMax);
    svg.append(createSvgElement("line", {
      class: "polar-spoke",
      x1: centerX,
      y1: centerY,
      x2: spokeEnd.x,
      y2: spokeEnd.y,
      stroke: svgPalette.gridStroke,
      "stroke-width": 1.1
    }));
  }

  for (const tick of preparedPlot.ringTicks) {
    const ringStroke = tick.className === "polar-reference"
      ? svgPalette.referenceStroke
      : tick.className === "polar-outer-ring"
        ? svgPalette.axisStroke
        : svgPalette.gridStroke;
    const ringDash = tick.className === "polar-reference"
      ? "3 5"
      : tick.className === "polar-ring"
        ? "5 6"
        : null;
    const ringRadius = radialScale(tick.value);
    svg.append(createSvgElement("circle", {
      class: tick.className,
      cx: centerX,
      cy: centerY,
      r: ringRadius,
      fill: "none",
      stroke: ringStroke,
      "stroke-width": tick.className === "polar-outer-ring" ? 1.6 : 1.1,
      "stroke-dasharray": ringDash
    }));

    if (!tick.label) {
      continue;
    }

    const label = createSvgElement("text", {
      class: "polar-label",
      x: centerX + 8,
      y: centerY - ringRadius - 6,
      fill: svgPalette.labelFill,
      "font-size": "11.5px",
      "font-weight": 700,
      "font-family": "Bahnschrift, Aptos, Segoe UI, sans-serif"
    });
    label.textContent = tick.label;
    svg.append(label);
  }

  for (const angle of angleLabels) {
    const angleRadians = (angle * Math.PI) / 180;
    const labelRadius = radius + 16;
    const label = createSvgElement("text", {
      class: "polar-angle-label",
      x: centerX + labelRadius * Math.sin(angleRadians),
      y: centerY - labelRadius * Math.cos(angleRadians) + 4,
      "text-anchor": "middle",
      fill: svgPalette.labelFill,
      "font-size": "10.5px",
      "font-weight": 700,
      "font-family": "Bahnschrift, Aptos, Segoe UI, sans-serif"
    });
    label.textContent = angle + "\u00b0";
    svg.append(label);
  }

  const axisLabel = createSvgElement("text", {
    class: "polar-label",
    x: centerX,
    y: height - 12,
    "text-anchor": "middle",
    fill: svgPalette.labelFill,
    "font-size": "11.5px",
    "font-weight": 700,
    "font-family": "Bahnschrift, Aptos, Segoe UI, sans-serif"
  });
  axisLabel.textContent = preparedPlot.yLabel;
  svg.append(axisLabel);

  for (const entry of preparedPlot.series) {
    const commands = entry.points.map((point, index) => {
      const position = polarToCartesian(point.angle_deg, point.display_value);
      return (index === 0 ? "M" : "L") + position.x.toFixed(2) + " " + position.y.toFixed(2);
    });

    svg.append(createSvgElement("path", {
      class: "series-line",
      d: commands.join(" "),
      fill: "none",
      stroke: entry.color,
      "stroke-width": 2.8,
      "stroke-linecap": "round",
      "stroke-linejoin": "round"
    }));
  }

  const crosshair = createSvgElement("line", {
    class: "crosshair",
    x1: centerX,
    y1: centerY,
    x2: centerX,
    y2: centerY - radius,
    visibility: "hidden",
    stroke: svgPalette.crosshairStroke,
    "stroke-width": 1.2,
    "stroke-dasharray": "4 4"
  });
  svg.append(crosshair);

  const markerLayer = createSvgElement("g");
  svg.append(markerLayer);

  const overlay = createSvgElement("rect", {
    x: 0,
    y: 0,
    width,
    height,
    fill: "transparent"
  });
  overlay.style.cursor = "crosshair";
  svg.append(overlay);

  let pinned = false;

  function clearHover() {
    crosshair.setAttribute("visibility", "hidden");
    markerLayer.replaceChildren();
    updatePlotReadout(readout, null, [], false, mode);
  }

  function renderHover(angle, pinState) {
    const nearestAngle = findNearestNumber(sampleAngles, angle);

    if (nearestAngle === null) {
      clearHover();
      return;
    }

    const crosshairEnd = polarToCartesian(nearestAngle, yMax);
    const rows = [];
    markerLayer.replaceChildren();
    crosshair.setAttribute("visibility", "visible");
    crosshair.setAttribute("x1", centerX);
    crosshair.setAttribute("y1", centerY);
    crosshair.setAttribute("x2", crosshairEnd.x);
    crosshair.setAttribute("y2", crosshairEnd.y);

    for (const entry of preparedPlot.series) {
      const point = entry.points.find((candidate) => Number(candidate.angle_deg) === nearestAngle);

      if (!point) {
        continue;
      }

      const markerPoint = polarToCartesian(nearestAngle, point.display_value);
      const marker = createSvgElement("circle", {
        class: "hover-marker",
        cx: markerPoint.x,
        cy: markerPoint.y,
        r: 4,
        fill: entry.color,
        stroke: svgPalette.hoverMarkerStroke,
        "stroke-width": 1.5
      });

      markerLayer.append(marker);
      rows.push({
        channel: entry.channel,
        color: entry.color,
        normalised_db: Number(point.normalised_db),
        e_over_emax: Number(point.e_over_emax),
        rx_peak_dbm: Number(point.rx_peak_dbm)
      });
    }

    updatePlotReadout(readout, nearestAngle, rows, pinState, mode);
  }

  function eventToAngle(event) {
    const bounds = svg.getBoundingClientRect();
    const svgX = ((event.clientX - bounds.left) / (bounds.width || 1)) * width;
    const svgY = ((event.clientY - bounds.top) / (bounds.height || 1)) * height;
    const dx = svgX - centerX;
    const dy = svgY - centerY;
    return (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;
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

  for (const button of analyserElements.plotModeButtons) {
    button.addEventListener("click", () => {
      const nextMode = button.dataset.plotMode;

      if (!nextMode || nextMode === analyserState.plotDisplayMode) {
        return;
      }

      analyserState.plotDisplayMode = nextMode;
      updatePlotModeUi();

      if (analyserState.dataset) {
        renderPlotGrid(analyserState.dataset);
      }
    });
  }
}

function bindThemeControls() {
  if (!themeElements.toggleButton) {
    return;
  }

  themeElements.toggleButton.addEventListener("click", () => {
    applyTheme(uiState.theme === "dark" ? "light" : "dark");
  });
}

function initialiseCollapsedAnalyserPanels() {
  analyserElements.measurementDetailsPanel.open = false;
  analyserElements.testFolderPanel.open = false;
}

bindRoutes();
bindAnalyserControls();
bindThemeControls();
initialiseCollapsedAnalyserPanels();
applyTheme(readStoredTheme());
updatePlotModeUi();
renderRoute();
refreshMeasurementData();
window.setInterval(refreshMeasurementData, REFRESH_MS);
