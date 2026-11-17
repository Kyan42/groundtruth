# Expected discovery for cbay's cart properties

Written by hand from the code on 2026-11-09, before any discovery step was run against cbay. The discovery gate
compares the verifier's case plans with this. Source read: `cbay/app` at commit `ec14bff` (the original app, in
which the sold-out listing still shows an enabled Add to cart button).

## How an item gets into the cart

- One function changes the cart: `addToCart(listingId)` in `lib/cart.ts` (the only `writeState` that touches `cart`).
- One API route calls it: `POST /api/cart` in `app/api/cart/route.ts`.
- One component calls that route: `components/AddToCartButton.tsx`.
- One page renders that component: the listing page, `app/listings/[id]/page.tsx`, at `/listings/<id>`.

So there is exactly one way in through the pages (the Add to cart button on a listing page) and one direct way
(POST /api/cart). Things that are NOT ways to add to the cart: the home page (cards only link to listings, plus a
search form), the cart page (no controls, only a back link), Buy It Now (POST /api/orders creates an order and
never touches the cart), the watchlist toggle. `scripts/seed.mjs` rewrites the whole state file; it is the reset,
not a way to add an item.

## Branches inside addToCart

1. unknown listing id: refused (`not_found`, the route answers 404), cart unchanged
2. sold-out listing: refused (`sold_out`, the route answers 409), cart unchanged
3. item already in the cart: its quantity goes up by one
4. item not in the cart: a new line with quantity 1

The subtotal is computed in `getCartView` (`lib/cart.ts`) as the sum of price times quantity, and shown on `/cart`.

## Data

Eight listings, l1 to l8. Only l6 (Pirate Chest, Key Lost) is sold out. Cheapest l5 ($15.00), dearest l7 ($310.00),
prices with odd cents: l1 ($249.99), l6 ($75.25, sold out), l8 ($199.99).

## What a correct case plan must contain

| Property | Must have | Must not have |
|---|---|---|
| p0 "Adding an item to the cart raises the cart subtotal by the price shown on its listing." (no scope stated) | A browser case through the listing page's Add to cart button; a case where the item is already in the cart; a case for the sold-out item marked as not settled by the wording | A page entry point other than the listing page; a sold-out case marked inside the scope |
| p1 "Adding an in-stock item to the cart raises the cart subtotal by the price shown on its listing." | Browser cases through the listing page button, with the item absent and with it already present; more than one item | Any direct API case (this is not a "must never happen" property); a page entry point other than the listing page |
| p2 "A sold-out item can't be added to the cart, and trying to add it leaves the cart unchanged." | A browser case on the sold-out listing /listings/l6; a direct POST /api/cart case for the sold-out item | (nothing) |

## Expected verdicts on the original cbay

- p0: needs your decision (the sold-out case breaks the wording; every in-stock case holds)
- p1: holds
- p2: holds (the enabled button gets a refusal and the cart stays empty; the direct call gets 409)
