# Deploying the frontend: GitHub Pages compatibility, and why Cloudflare Pages is recommended instead

Companion to `docs/deployment_cloud_run.md` / `docs/deployment_supabase.md`.
This document exists because GitHub Pages turned out **not** to be a clean
fit for this frontend's current setup -- documented here exactly, per the
task's own instruction, rather than forced through with workarounds baked
into the app.

## The exact issue with GitHub Pages

Checked directly against this repo's current configuration
(`frontend/vite.config.ts`, `frontend/src/main.tsx`):

1. **No `base` path is configured** (`vite.config.ts` has no `base` key,
   so it defaults to `/`, i.e. "served from the domain root"). A GitHub
   Pages **project site** (as opposed to a user/org root site) is served
   from `https://<user>.github.io/<repo-name>/` -- a subpath, not the
   domain root. Without `base: '/energy-intelligence-platform/'` set,
   every built asset reference (JS/CSS bundle URLs Vite writes into
   `index.html`) would 404, because they'd be requested from
   `/assets/...` instead of `/energy-intelligence-platform/assets/...`.
2. **`BrowserRouter` is used** (`frontend/src/main.tsx`), not
   `HashRouter`. `BrowserRouter` needs real server-side URL rewriting to
   support a direct load of (or refresh on) a client-side route like
   `/external-forecast` -- the server must serve `index.html` for *any*
   path and let React Router take over client-side. **GitHub Pages has no
   such rewrite capability** -- it is pure static file serving with no
   server-side configuration surface (no `_redirects`-style file, no
   custom rewrite rules). A direct visit to
   `https://<user>.github.io/energy-intelligence-platform/external-forecast`
   would 404 at the CDN level, before React ever loads.

Combined, these are exactly the "GitHub Pages routing creates unnecessary
complexity" scenario the task anticipated. The two ways to actually make
GitHub Pages work both require real changes to working code:

- **Switch to `HashRouter`**: makes routing purely client-side
  (`/#/external-forecast`), which GitHub Pages can serve trivially (every
  URL a browser requests is just `/index.html` with a fragment the server
  never sees) -- but changes every route's real URL shape, a visible,
  permanent change to how this app's six pages are linked/shared/bookmarked.
- **Keep `BrowserRouter` + add a `404.html` SPA-fallback shim**: GitHub
  Pages serves a custom `404.html` for any unmatched path; a small script
  in it captures the intended path and redirects to `index.html`, which
  then restores the real URL via `history.replaceState`. Works, but adds
  a non-obvious extra file and a redirect round-trip purely to compensate
  for the host's limitation -- not a change to the app itself, but real
  added complexity in the deployment surface.

## Recommended instead: Cloudflare Pages

Cloudflare Pages needs **zero frontend code changes** for this app:

- Serves from the site root by default (a `*.pages.dev` subdomain, or a
  custom domain) -- no `base` path issue.
- Natively supports a SPA fallback rewrite via one small config file (see
  below) -- `BrowserRouter` works exactly as it already does locally,
  URLs stay exactly as they are now.
- Free tier includes unlimited requests/bandwidth for static sites (unlike
  GitHub Pages' soft bandwidth guidance, though neither matters at
  portfolio-demo traffic levels).

### Setup

1. https://dash.cloudflare.com -> **Workers & Pages** -> **Create** ->
   **Pages** -> **Connect to Git** -> select
   `BiniyamAFirde/energy-intelligence-platform`.
2. Build settings:
   - Framework preset: **Vite** (or manual: build command
     `npm install && npm run build`, output directory `dist`).
   - Root directory: `frontend`.
3. Environment variable (build-time -- Vite bakes `VITE_*` vars in at
   build, not runtime, same as documented for any other host): (see the
   README's Deployment section)

   ```
   VITE_API_BASE_URL = https://<your-cloud-run-backend-url>
   ```

4. Add `frontend/public/_redirects` (Cloudflare Pages' SPA-fallback
   mechanism -- one line, no JS shim, no route-shape change):

   ```
   /*    /index.html   200
   ```

   This is the **one** file this repo would need to add to support any
   host requiring an explicit SPA-fallback rule (Cloudflare Pages,
   Netlify, and others all use this exact `_redirects` convention) --
   deliberately not added yet in this pass, since no such host has
   actually been chosen/deployed to; add it at the point you actually set
   up Cloudflare Pages (or whichever host you pick), not preemptively.
5. Deploy. Cloudflare auto-builds and redeploys on every push to `main` by
   default, matching the "push to deploy" workflow the rest of this
   project already uses.

## If you still want GitHub Pages

The procedure, if you decide the URL-shape tradeoff (or the 404.html
shim) is acceptable:

**Option A -- HashRouter** (smaller, more mechanical change):

```ts
// frontend/src/main.tsx
import { HashRouter } from 'react-router-dom'   // was BrowserRouter
```

```ts
// frontend/vite.config.ts
export default defineConfig({
  base: '/energy-intelligence-platform/',
  // ...rest unchanged
})
```

**Option B -- keep BrowserRouter, add a 404.html redirect shim** -- more
code (a redirect script in `frontend/public/404.html` plus a small
matching decode snippet loaded before the app mounts), not written here
since Cloudflare Pages (above) achieves the same real-URL outcome with
none of this. Only worth doing if GitHub Pages specifically (as opposed
to "any static host") is a hard requirement.

Either option is a **real, visible change to working routing behavior**
-- per the task's constraints, this was deliberately not applied without
you choosing it first. Neither change has been made in this repository as
of this document.

## Either way

**No public URL has been created or verified for this frontend as part of
this work.** Whichever host you choose, the verification step is the
same: open the deployed URL, confirm all six pages load (Dashboard,
Building Detail, Forecasting, Anomaly Monitoring, Anomaly Detail, External
Forecast), confirm a direct load of `/external-forecast` (not just
client-side navigation to it) works, and confirm the Network tab shows
requests going to your real Cloud Run backend URL, not `localhost`.
