import './Controls.css'

interface DateRangePickerProps {
  start: string | undefined
  end: string | undefined
  onChange: (range: { start: string | undefined; end: string | undefined }) => void
}

export function DateRangePicker({ start, end, onChange }: DateRangePickerProps) {
  return (
    <div className="date-range-picker">
      <label className="control-label">
        <span className="visually-hidden">Start date</span>
        <input
          type="date"
          className="control-input"
          value={start ?? ''}
          max={end}
          onChange={(e) => onChange({ start: e.target.value || undefined, end })}
        />
      </label>
      <span className="date-range-sep">&rarr;</span>
      <label className="control-label">
        <span className="visually-hidden">End date</span>
        <input
          type="date"
          className="control-input"
          value={end ?? ''}
          min={start}
          onChange={(e) => onChange({ start, end: e.target.value || undefined })}
        />
      </label>
    </div>
  )
}
