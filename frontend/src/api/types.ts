// Mirrors backend/src/energy_platform/schemas/*.py field-for-field.
// Do not invent fields here that the API doesn't actually return.

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

// --- sites ---

export interface Site {
  site_id: number
  site_code: string
  timezone: string
  created_at: string
}

// --- buildings ---

export interface Building {
  building_id: number
  building_code: string
  site_id: number
  primary_use: string | null
  area_sqm: number | null
  sub_primary_use: string | null
  latitude: number | null
  longitude: number | null
  year_built: number | null
  number_of_floors: number | null
  occupants: number | null
  industry: string | null
  subindustry: string | null
  heating_type: string | null
  eui: number | null
  site_eui: number | null
  source_eui: number | null
  leed_level: string | null
  energy_star_rating: number | null
  created_at: string
}

// --- sensors ---

export interface Sensor {
  sensor_id: number
  building_id: number
  meter_type: string
  unit: string
  created_at: string
}

// --- energy ---

export interface EnergyMeasurement {
  ts: string
  consumption_kwh: number | null
}

export interface EnergyAggregatePoint {
  period_start: string
  total_kwh: number | null
  mean_kwh: number | null
  observation_count: number
}

export interface BuildingSummary {
  building_id: number
  building_code: string
  total_observations: number
  mean_consumption_kwh: number | null
  min_consumption_kwh: number | null
  max_consumption_kwh: number | null
  stddev_consumption_kwh: number | null
  first_timestamp: string
  last_timestamp: string
  missing_observations: number
}

export type Granularity = 'hourly' | 'daily' | 'weekly' | 'monthly'

// --- analytics ---

export interface BuildingComparisonRow {
  building_id: number
  building_code: string
  site_id: number
  primary_use: string | null
  area_sqm: number | null
  mean_kwh: number | null
  median_kwh: number | null
  total_kwh: number | null
  peak_kwh: number | null
  std_kwh: number | null
  coefficient_of_variation: number | null
}

export interface BuildingAnalyticsSummary {
  building_id: number
  building_code: string
  total_observations: number
  mean_kwh: number | null
  median_kwh: number | null
  min_kwh: number | null
  max_kwh: number | null
  stddev_kwh: number | null
  total_kwh: number | null
  coefficient_of_variation: number | null
  missing_observations: number
  first_timestamp: string
  last_timestamp: string
}

export interface HourOfDayPoint {
  hour_of_day: number
  mean_kwh: number | null
  observation_count: number
}

export interface DayOfWeekPoint {
  day_of_week: number // Postgres convention: 0=Sunday .. 6=Saturday
  mean_kwh: number | null
  observation_count: number
}

export interface MonthPoint {
  month: number // 1=January .. 12=December
  mean_kwh: number | null
  total_kwh: number | null
  observation_count: number
}

export interface WeekdayWeekendPoint {
  is_weekend: boolean
  mean_kwh: number | null
  observation_count: number
}

export interface BuildingProfile {
  building_id: number
  timezone: string
  hour_of_day: HourOfDayPoint[]
  day_of_week: DayOfWeekPoint[]
  month: MonthPoint[]
  weekday_weekend: WeekdayWeekendPoint[]
}

export interface PeakPoint {
  ts: string
  consumption_kwh: number
}

export interface BuildingPeaks {
  building_id: number
  peak_kwh: number
  peak_timestamp: string
  top_peaks: PeakPoint[]
}

// --- anomalies ---

export type DetectorMethod = 'data_quality' | 'behavioral' | 'forecast_residual' | 'isolation_forest'
export type Severity = 'low' | 'medium' | 'high'

export interface Alert {
  id: number
  sensor_id: number
  ts: string
  method: DetectorMethod
  anomaly_type: string
  detector_version: string
  severity: Severity
  score: number
  expected_value: number | null
  actual_value: number | null
  residual: number | null
  explanation: string
  resolved: boolean
  created_at: string
}

export interface AlertSummary {
  total_alerts: number
  unresolved_alerts: number
  counts_by_method: Record<string, number>
  counts_by_anomaly_type: Record<string, number>
  counts_by_severity: Record<string, number>
}

// --- forecast ---

export interface ForecastPoint {
  sensor_id: number
  target_ts: string
  generated_at: string
  model_name: string
  model_version: string
  horizon: number | null
  predicted_kwh: number
  actual_kwh: number | null
  residual: number | null
}
