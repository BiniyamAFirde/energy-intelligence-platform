import { useEffect, useRef, useState, type DependencyList } from 'react'

export interface UseApiState<T> {
  data: T | null
  loading: boolean
  error: Error | null
}

/**
 * Runs `fetcher` whenever `deps` changes, tracking loading/error/data.
 * `fetcher`'s own identity is intentionally NOT part of the dependency
 * check (it's typically a fresh inline closure every render) -- callers
 * control re-fetching explicitly via `deps`, the same contract as
 * useEffect itself. A monotonically increasing request id guards against
 * a slow, stale request overwriting a newer one's result.
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: DependencyList): UseApiState<T> {
  const [state, setState] = useState<UseApiState<T>>({ data: null, loading: true, error: null })
  const requestId = useRef(0)

  // react/exhaustive-deps is disabled project-wide for this one file in
  // .oxlintrc.json -- see that file's comment for why (inline disable
  // comments proved unreliable for this rule in the installed oxlint
  // version; this generic hook's `deps` passthrough is the only place in
  // the codebase that needs the exemption).
  useEffect(() => {
    const id = ++requestId.current
    setState((prev) => ({ data: prev.data, loading: true, error: null }))

    fetcher()
      .then((data) => {
        if (requestId.current === id) setState({ data, loading: false, error: null })
      })
      .catch((error: unknown) => {
        if (requestId.current === id) {
          setState({ data: null, loading: false, error: error instanceof Error ? error : new Error(String(error)) })
        }
      })
  }, deps)

  return state
}
