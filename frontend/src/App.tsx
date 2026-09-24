import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { LoadingState } from './components/StateViews'

// Route-level code splitting: each page (and its own chart-heavy deps)
// loads only when its route is visited, instead of one large upfront bundle.
const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })))
const BuildingDetail = lazy(() => import('./pages/BuildingDetail').then((m) => ({ default: m.BuildingDetail })))
const Forecasting = lazy(() => import('./pages/Forecasting').then((m) => ({ default: m.Forecasting })))
const AnomalyMonitoring = lazy(() =>
  import('./pages/AnomalyMonitoring').then((m) => ({ default: m.AnomalyMonitoring })),
)
const AnomalyDetail = lazy(() => import('./pages/AnomalyDetail').then((m) => ({ default: m.AnomalyDetail })))
const ExternalForecast = lazy(() =>
  import('./pages/ExternalForecast').then((m) => ({ default: m.ExternalForecast })),
)

export function App() {
  return (
    <Suspense fallback={<LoadingState label="Loading page..." />}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/buildings/:buildingId" element={<BuildingDetail />} />
          <Route path="/forecasting" element={<Forecasting />} />
          <Route path="/anomalies" element={<AnomalyMonitoring />} />
          <Route path="/anomalies/:alertId" element={<AnomalyDetail />} />
          <Route path="/external-forecast" element={<ExternalForecast />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
