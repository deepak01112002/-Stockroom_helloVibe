# Stockroom design

## Current service design

PostgreSQL is the inventory source of truth. Each `StockLevel` enforces
`held + sold <= on_hand`; availability is `on_hand - held - sold`. A hold is
allocated to exactly one product/warehouse row. Creation locks that stock row;
all existing-hold transitions lock `Hold` and then `StockLevel`. The stable
order avoids deadlock cycles.

Expiry workers claim due holds by expiry-time/ID using `FOR UPDATE SKIP
LOCKED`. They release held stock and expire a claimed hold in one transaction.
An uncommitted crash rolls back both the row locks and state change, allowing a
different worker to retry. Webhooks verify raw-body HMAC before parsing, record
a unique provider event, and process the payment state in the same transaction.
Duplicate event IDs return their stored result; late success never re-reserves
stock or creates a new order.

Supplier stock is split as Warehouse A `ceil(total / 2)` and Warehouse B
`floor(total / 2)`. Per-product savepoints isolate malformed supplier records
and unsafe reconciliations. PostgreSQL fixed-minute buckets enforce the shared
hold-create limit across API instances.

## Azure production topology

Deploy the API and expiry worker as separate Azure Container Apps revisions in
one Container Apps Environment. The API scales on HTTP concurrency; the worker
uses a small fixed replica count because `SKIP LOCKED` makes duplicate workers
safe. Azure Database for PostgreSQL Flexible Server is the authoritative,
zone-redundant database. Private endpoints, a delegated subnet, and managed
identities keep database traffic off the public internet.

Use Azure Key Vault for `PAYMENT_WEBHOOK_SECRET`, database credentials if not
using Entra authentication, and supplier configuration. Container Apps access
secrets through managed identity; secrets are never placed in images, source,
or logs.

For payment delivery, the public webhook only verifies the HMAC and writes an
idempotent inbound event/outbox record, then acknowledges promptly. Azure
Service Bus sits before business processing: a worker consumes the event,
performs the hold/order transaction, and marks it completed. The event ID is
still unique in PostgreSQL, so Service Bus at-least-once delivery is harmless.
Dead-letter queues expose malformed or repeatedly failing events for operator
review. This decouples provider retries from database contention and lets API
instances remain responsive during payment bursts.

## Festival sale: 500 hold requests/second on 20 products

The first bottleneck is not API CPU but row contention: popular product and
warehouse rows serialize because a correct reservation must atomically check
and increment `held`. At 500 requests/second concentrated on 20 products,
horizontal API scaling adds connections but cannot make one stock row process
in parallel. PostgreSQL connection exhaustion and lock wait latency follow.

Initial mitigations are per-client and per-product admission limits, short
transactions, a bounded connection pool, and monitoring of lock waits,
deadlocks, saturation, and 429 rates. For sustained load, split a product's
inventory into more allocation rows/shards while preserving a deterministic
allocation strategy, or use conditional atomic updates with retries to reduce
lock hold time. Do not replace PostgreSQL inventory truth with in-memory locks
or an eventually consistent cache. Service Bus is appropriate for payment work,
but hold admission must remain synchronous so customers receive an accurate
availability answer.
