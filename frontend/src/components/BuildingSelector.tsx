import { listBuildings } from '../api/buildings'
import { useApi } from '../hooks/useApi'
import './Controls.css'

interface BuildingSelectorProps {
  value: number | undefined
  onChange: (buildingId: number) => void
}

export function BuildingSelector({ value, onChange }: BuildingSelectorProps) {
  const { data, loading } = useApi(() => listBuildings({ limit: 200 }), [])
  const buildings = data?.items ?? []

  return (
    <select
      className="control-select"
      value={value ?? ''}
      disabled={loading || buildings.length === 0}
      onChange={(e) => onChange(Number(e.target.value))}
      aria-label="Select building"
    >
      {value === undefined && <option value="">{loading ? 'Loading buildings...' : 'Select a building'}</option>}
      {buildings.map((b) => (
        <option key={b.building_id} value={b.building_id}>
          {b.building_code}
        </option>
      ))}
    </select>
  )
}
