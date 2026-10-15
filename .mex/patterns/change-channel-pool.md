---
name: change-channel-pool
description: Change the warming channel pool search, its filters, the refill or the consent pull.
last_updated: 2026-10-14
edges:
  - target: context/runtime-warming.md
    condition: warming runtime invariants, budget or visits
  - target: context/architecture.md
    condition: layer boundaries or gateways
---

# Change the Channel Pool Search

Invariants to keep:
1. One engine (`_pool_hunt`) and one filter set (`_pool_filters`) for the button, the refill and a warming account's own search. Every fit is `approved` at once; a found row the operator removes stays `removed`, so no search re-adds it; typing it again restores it.
2. A rate-limit read error ends the search at once; nothing reads past it.
3. A pool search never reads with a warming account (a spam-block stop runs no loop and is not one: a spam block only shuts messages to strangers), the running listener, a discovery/parser run or a live bulk-message account. A busy account (commenting, neuroshilling, broadcast, masstagging) is taken only with the operator's consent for the work they were shown.
4. A pool search holds the account lifecycle lock for its whole length and lends the account in `services._account_owner` beside the claim, never over it: owners wait at their next send; neurocomment selection, discovery and campaign starts read the loan and pick another account instead of waiting. The loan ends in `finally`.
5. A warming account searches only for itself, inside a normal/binge visit, from its safe phase; each read is booked on the visit budget before dispatch through the leased seam; a flood stops the visit; joins of its finds obey the join cap.
6. The refill searches one short language per pass, any language at most once per interval; with no free account it leaves one request for the board, never takes an account.

Verify with the `test_pool_*` and `test_self_search*` suites.
