# Host the recorded demonstration on Cloudflare Pages

The recorded demo uses static hosting. Visitors receive the reviewed application, public semantic case, and attributed map geometry. Playback and the two choices run independently in each browser. The laptop and live digital-twin services are not involved after upload. The public case is downloadable by visitors. Original recordings, credentials, diagnostics, and the development repository stay local. [Demo preparation](browser-demo.md) · [Privacy boundary](../privacy.md)

**Public site:** [drivetwin-demo.pages.dev](https://drivetwin-demo.pages.dev/). The uploaded package passed anonymous HTTPS verification of the deployed bytes, response headers, both decisions, and nine tabs on November 18, 2026 (UTC). [Verification record](../verification/browser-demo.md#public-cloudflare-deployment)

## Prepare and check the upload

Run in Windows PowerShell from the project root. These commands start no bus and open no visible browser:

```powershell
Set-Location '<checkout>'
python scripts/demo/cloudflare_bundle.py
```

The preparer uses the same selected current receipt as the local viewer, checks its entire file inventory and actual-case browser receipt, and rejects extra, missing, changed, or redirected files. Workspaces without a current receipt use the historical latest-READY fallback. The current receipt can bind a browser report elsewhere in ignored `.private` storage; an older explicitly selected preparation uses its sibling `browser/browser-report.json`. A changed current receipt fails instead of silently selecting an older candidate.

Preparation creates a new ignored `.private/browser-demo/cloudflare-…` directory. A new privacy and pinned secret scan checks the upload contents. The ZIP contains its own root `index.html`; no parent directory, raw evidence, or source repository is included. Existing candidates are preserved. Use `--site` to choose a particular reviewed candidate for this invocation; this does not change the local current selection.

The printed directory contains `drivetwin-demo.zip`, its uploadable `site` tree, and a private `upload-receipt.json`. Use that directory below:

```powershell
node qualification/browser/demo-hosting-check.mjs --bundle '.private/browser-demo/cloudflare-REPLACE_WITH_PREPARED_ID'
```

Expected result: `passed: true` and `mode: local-pages-behavior`. The headless checker verifies all file fingerprints, both recorded choices at second 30, held decision controls, measured charging, nine tabs, both endings, narrow-screen overflow, same-origin requests, and absence of script or content-policy errors. The local HTTP harness reproduces the subset of Pages behavior used here; it does not establish that an actual Cloudflare deployment works. Reports and screenshots stay beside the upload receipt. Failed attempts are retained.

The upload adds `_headers` with same-origin scripts and connections, disabled embedding, MIME protection, and restricted browser permissions. Inline styles remain allowed for React/Leaflet positioning; inline scripts are not allowed. JSON case files and entry pages require cache revalidation, while content-named assets receive immutable caching. The root `404.html` disables Pages' automatic SPA fallback so a missing JSON asset receives a real 404. No Worker, Functions, database binding, or environment secret is needed. [Pages headers](https://developers.cloudflare.com/pages/configuration/headers/) · [Serving and caching](https://developers.cloudflare.com/pages/configuration/serving-pages/)

## Upload using the account dashboard

The account owner signs in privately. Browser sign-in is not a background API credential. The ZIP upload avoids sharing passwords or creating an API token.

1. Open the [Cloudflare dashboard](https://dash.cloudflare.com/) and select **Workers & Pages**.
2. Select **Create application**, choose **Pages**, and choose its **Drag and drop** / direct upload option. Dashboard labels may vary. Use **Get started** if shown.
3. Enter an available project name, such as `drivetwin-demo`. Upload only the prepared `drivetwin-demo.zip`.
4. Select **Deploy site** / **Save and Deploy**. Wait for success and copy the stable production `https://PROJECT.pages.dev/` address.

The existing built bundle needs no build command or Git connection. Direct Upload projects cannot later switch to built-in Git integration; a new project is required for that integration. A later custom domain is optional and follows its own authorized DNS setup. [Official Direct Upload procedure](https://developers.cloudflare.com/pages/get-started/direct-upload/)

## Verify the public link

After upload, run with the same private upload directory and the actual HTTPS site root:

```powershell
node qualification/browser/demo-hosting-check.mjs --bundle '.private/browser-demo/cloudflare-REPLACE_WITH_PREPARED_ID' --url 'https://PROJECT.pages.dev/'
```

Expected result: `passed: true` and `mode: public-https`. This mode anonymously downloads and fingerprints the deployed files, follows same-origin HTML redirects, checks security/cache headers and real 404 responses, and runs both choices in fresh headless browser contexts. It uses no saved browser login. An account login or access gate is a failed public deployment. A failed remote check leaves its report and does not change or automatically redeploy the site.

After that check, add the actual production link to the README and record the tested upload and result in the [verification guide](../verification/browser-demo.md). The owner may link to it from a personal website. No website or DNS change is part of the initial Pages upload.

## Unlisted portfolio deployment

The separate unlisted portfolio build serves the demo files directly under the website's origin. It does not redirect to Pages or embed an externally hosted frame. It uses the same reviewed case and relative asset paths, with noindex and supported content-policy metadata added to its HTML. The website's homepage, navigation, and sitemap do not link to the entry. The location and exact deployment receipt remain in the ignored `.private/browser-demo/website-direct-20261118/` handoff so this guide does not advertise the route.

Cloudflare's `_headers` file is provider-specific; the portfolio deployment uses HTML metadata for supported directives instead. Response-only directives such as `frame-ancestors` are not claimed through a meta policy. The deployed files require their own inventory and anonymous browser check after each update. The original Cloudflare deployment retains its independently verified package and update procedure below.

## Update or roll back

For an update, prepare and verify a new accepted demo candidate, create a fresh upload directory, and use **Create a new deployment** in the existing Pages project. Upload the new ZIP to production and repeat the anonymous public check. Preserve previous ZIPs and receipts. An open visitor session keeps its already-loaded outcome data until refresh. If an update fails, use Cloudflare's deployment history to roll back to a known verified deployment and recheck the production link. [Pages rollbacks](https://developers.cloudflare.com/pages/configuration/rollbacks/)
