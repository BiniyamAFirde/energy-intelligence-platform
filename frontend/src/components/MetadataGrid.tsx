import type { ReactNode } from 'react'
import './MetadataGrid.css'

export interface MetadataField {
  label: string
  value: ReactNode
}

export function MetadataGrid({ fields }: { fields: MetadataField[] }) {
  return (
    <dl className="metadata-grid">
      {fields.map((field) => (
        <div key={field.label} className="metadata-item">
          <dt>{field.label}</dt>
          <dd>{field.value}</dd>
        </div>
      ))}
    </dl>
  )
}
