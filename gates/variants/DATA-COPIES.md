# The data-tools copies of cbay

Three copies under `gates/work/` (not in git), used by the `data` stream of `gates/props_gate.py`:

| Copy | What it is |
|---|---|
| `cbay-data-clean` | cbay plus its data tools (`data-tools.patch`, `data-tools/data.yml`). No bug. |
| `cbay-data-bug` | the clean copy plus `data-bug.patch`: in `lib/cart.ts`, a listing priced at $1,000.00 or more enters the cart at a hundredth of its price. The seed's highest price is $310.00, so only made data shows it. |
| `cbay-data-bug-no-tool` | the bug copy, but `data-tools/data-no-listing-tool.yml` declares only `make_order`, and `scripts/groundtruth/make-listing.mjs` is removed. A listing of $1,000.00 or more cannot be set up. |

The data tools patch adds made listings to the state file (`listings` in `data/state.json`, through `addListing` in
`lib/store.ts`), lists them on the home page and listing pages (`allListings` in `lib/catalog.ts`), and adds
`scripts/groundtruth/` (`make-listing.mjs` imports `lib/store.ts` directly, which needs Node 22.18 or later for type
stripping; `make-order.mjs` posts to the shop's own `/api/orders`). The reset command (`node scripts/seed.mjs --reset`)
rewrites the state file, so made listings disappear with it.

## Rebuild

From `gates/work/`, with Node 22 first on PATH (`export PATH=/opt/homebrew/opt/node@22/bin:$PATH`) and `V` set to
this folder (`gates/variants` of the checkout):

```bash
rm -rf cbay-data-clean cbay-data-bug cbay-data-bug-no-tool

# clean
cp -Rc ../../../cbay/app cbay-data-clean
rm -rf cbay-data-clean/.groundtruth          # the reference's runs, env.json, decisions and other features
mkdir -p cbay-data-clean/.groundtruth/features
(cd cbay-data-clean && patch -p1 < $V/data-tools.patch)
cp $V/data-tools/data.yml cbay-data-clean/.groundtruth/data.yml
(cd cbay-data-clean && node scripts/seed.mjs --reset && npm run build)

# bug
cp -Rc cbay-data-clean cbay-data-bug
(cd cbay-data-bug && patch -p1 < $V/data-bug.patch && npm run build)

# bug, no listing tool
cp -Rc cbay-data-bug cbay-data-bug-no-tool
rm cbay-data-bug-no-tool/scripts/groundtruth/make-listing.mjs
cp $V/data-tools/data-no-listing-tool.yml cbay-data-bug-no-tool/.groundtruth/data.yml
(cd cbay-data-bug-no-tool && npm run build)
```

Each copy needs `.groundtruth/features/cart.yml` with p1 only, as in `../cbay/app/.groundtruth/features/cart.yml`. The
stream writes it before each run (`props_lib.write_feature`), so a fresh copy can start without it.

Rebuild after changing a copy's source: each copy's `.next` must come from its own folder.

## Checking a copy by hand

Start it on a free port (`node node_modules/next/dist/bin/next start --port N`), then run a tool the way the tool
server does, from the copy's folder:

```bash
echo '{"title": "Gold Doubloon Hoard", "price_cents": 125000}' \
  | GROUNDTRUTH_APP_URL=http://127.0.0.1:N node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON scripts/groundtruth/make-listing.mjs
# {"id":"u1","page":"/listings/u1","url":"http://127.0.0.1:N/listings/u1","title":"Gold Doubloon Hoard","price":"$1,250.00","sold_out":false}
curl -s -XPOST localhost:N/api/cart -H 'content-type: application/json' -d '{"listingId":"u1"}'
curl -s localhost:N/api/cart     # subtotalCents 125000 on the clean copy, 1250 on the bug copy
node scripts/seed.mjs --reset    # /listings/u1 is a 404 again
```

On 2026-11-10, through the cart API (not a browser click): every seeded in-stock listing (added once and again) and
four Swordfish ($1,240.00 in one line) came out right on all three copies. On the clean and bug copies a made $999.99
listing was right; made $1,000.00 and $1,250.00 listings were right on the clean copy and wrong (+$10.00, +$12.50) on
the bug copy; a made sold-out listing was refused with 409; reset removed every made listing. On the no-tool copy
`make_listing` is not declared, and `make_order` placed an order for l2 and was refused for the sold-out l6.

`make-listing.mjs` is run with bare `node`, as the tool server runs commands in a shell with its own environment:
that `node` must be 22.18 or later (on this machine `/opt/homebrew/bin/node` is 22.23).
