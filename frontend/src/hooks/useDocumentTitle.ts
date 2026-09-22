import { useEffect } from 'react'

/** Sets the browser tab title for the current page. A single static
 * <title> in index.html would otherwise apply to all five routes in this
 * SPA, which is both a minor accessibility gap (screen reader users and
 * anyone with many tabs open rely on distinct titles) and just bad
 * practice for a portfolio project meant to be looked at closely. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    const previous = document.title
    document.title = `${title} — Energy Intelligence Platform`
    return () => {
      document.title = previous
    }
  }, [title])
}
